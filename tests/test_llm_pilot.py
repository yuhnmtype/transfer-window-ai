"""Tests for src/llm_pilot.py on synthetic data; no network (fake model where needed)."""
import json

import numpy as np
import pandas as pd
import pytest

import llm_pilot as pilot


def dataset():
    rows = []
    for i in range(30):
        split = "train" if i < 20 else ("calibration" if i < 25 else "test")
        rows.append({"player_id": 100 + i, "transfer_date": pd.Timestamp("2020-07-01") + pd.Timedelta(days=i),
                     "split": split, "position": "Attack", "age": 20 + i / 10,
                     "mv_eur": 5e6 + i * 1e6, "window": "S2020"})
    return pd.DataFrame(rows)


def test_pilot_rows_are_train_only_and_seeded():
    data = dataset()
    rows = pilot.select_pilot_rows(data)
    assert len(rows) == 10 and set(rows["split"]) == {"train"}
    # Same rows as an independent draw with the documented seed on the 20 train rows
    expected = sorted(np.random.default_rng(20261017).choice(20, 10, replace=False).tolist())
    assert list(rows["player_id"]) == [100 + i for i in expected]
    assert list(pilot.select_pilot_rows(data)["player_id"]) == list(rows["player_id"])


def test_pilot_refuses_more_than_10_rows():
    with pytest.raises(ValueError):
        pilot.select_pilot_rows(dataset(), n=11)


@pytest.mark.parametrize("split", ["calibration", "test"])
def test_pilot_refuses_a_non_train_row(split):
    rows = dataset()
    rows = pd.concat([rows[rows["split"] == "train"].head(3), rows[rows["split"] == split].head(1)])
    with pytest.raises(ValueError, match="only uses train rows"):
        pilot.assert_train_rows(rows)
    with pytest.raises(ValueError):
        pilot.run_pilot(rows, 1.25, 0.714309272881752, {}, None, None)   # refuses before any call


def test_pilot_refuses_to_overwrite(tmp_path, monkeypatch, capsys):
    existing = tmp_path / "llm_pilot_run1.json"
    existing.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(pilot, "PILOT_RESULTS", existing)
    assert pilot.main() == 1
    assert "never overwritten" in capsys.readouterr().out
    assert existing.read_text(encoding="utf-8") == "{}"


def test_fee_parameters_read_from_the_results_file():
    m, q, md5 = pilot.fee_parameters()
    path = pilot.FEE_RESULTS
    data = json.loads(path.read_text(encoding="utf-8"))
    assert (m, q) == (data["train_median_ratio_m"], data["conformal_q"])
    assert len(md5) == 32


def test_run_pilot_with_fake_model(tmp_path):
    from test_llm_agents import fake_model, ok
    import llm_agents as la
    rows = pilot.select_pilot_rows(dataset(), n=2)
    # 2 rows x 2 roles = 4 calls; the 3rd answer is out of band, so it is retried
    model = fake_model([ok(1.1), ok(0.9), ok(5.0), ok(1.2), ok(1.0)])
    record = pilot.run_pilot(rows, 1.25, 0.714309272881752, la.load_templates(),
                             la.DiskCache(tmp_path), model, "fake-model")
    assert record["counts"]["all"] == {"calls": 4, "valid": 4, "retried": 1, "clipped": 0,
                                       "fallback": 0, "valid_share": 1.0}
    assert record["band"] == [0.4895, 2.0428]
    assert all(r["split"] == "train" for r in record["rows"])
