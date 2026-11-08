"""Train and evaluate ML models that predict the average vehicle wait of each
signal-control mode from the traffic demand, and use them to choose the mode.

Input : ml/data/ml_dataset.csv (from ml_generate_dataset.py)
Output: ml/data/ml_model_results.csv, ml_mode_selection.csv, ml_test_predictions.csv
        displays the evaluation plots in a Matplotlib window

Run (from the project root):  python ml/ml_train_evaluate.py
Requires: numpy, scikit-learn, matplotlib  (pip install numpy scikit-learn matplotlib)
"""
import csv
import os

import matplotlib.pyplot as plt
import numpy as np
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import RandomForestRegressor
from sklearn.linear_model import LinearRegression
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.model_selection import KFold, cross_val_score, train_test_split
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
SEED = 42


def load():
    with open(os.path.join(DATA, "ml_dataset.csv")) as fh:
        rows = list(csv.DictReader(fh))
    N = np.array([[float(r[k]) for k in ("N_vph", "S_vph", "E_vph", "W_vph")] for r in rows])
    # engineered features: per-phase critical demand, total, imbalance
    ns, ew = N[:, :2].max(1), N[:, 2:].max(1)
    X = np.column_stack([N, ns, ew, N.sum(1), np.abs(ns - ew)])
    names = ["N_vph", "S_vph", "E_vph", "W_vph", "NS_critical", "EW_critical", "total_vph", "imbalance"]
    y_fixed = np.array([float(r["fixed_avg_wait_s"]) for r in rows])
    y_adapt = np.array([float(r["adaptive_avg_wait_s"]) for r in rows])
    return X, names, y_fixed, y_adapt


def models():
    return {
        "Mean baseline": DummyRegressor(),
        "Linear regression": make_pipeline(StandardScaler(), LinearRegression()),
        "Random forest": RandomForestRegressor(n_estimators=300, min_samples_leaf=2, random_state=SEED),
        "Neural network (MLP)": make_pipeline(
            StandardScaler(),
            MLPRegressor(hidden_layer_sizes=(32, 16), alpha=1e-3, learning_rate_init=0.01,
                         max_iter=3000, early_stopping=True, random_state=SEED)),
    }


def main():
    X, names, yf, ya = load()
    idx = np.arange(len(X))
    tr, te = train_test_split(idx, test_size=0.2, random_state=SEED)
    cv = KFold(5, shuffle=True, random_state=SEED)
    res, preds = [], {}
    for target, y in (("fixed", yf), ("adaptive", ya)):
        for name, m in models().items():
            cv_mae = -cross_val_score(m, X[tr], y[tr], cv=cv, scoring="neg_mean_absolute_error")
            m.fit(X[tr], y[tr])
            p = m.predict(X[te])
            preds[(target, name)] = p
            res.append([target, name, round(cv_mae.mean(), 3), round(cv_mae.std(), 3),
                        round(mean_absolute_error(y[te], p), 3),
                        round(mean_squared_error(y[te], p) ** 0.5, 3),
                        round(r2_score(y[te], p), 3)])
    with open(os.path.join(DATA, "ml_model_results.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["target_mode", "model", "cv5_mae_mean_s", "cv5_mae_sd_s",
                    "test_mae_s", "test_rmse_s", "test_r2"])
        w.writerows(res)

    # --- mode selection on the held-out test scenarios ---
    true_f, true_a = yf[te], ya[te]
    oracle = np.minimum(true_f, true_a)
    sel_rows = [["Always fixed-time", "-", round(true_f.mean(), 3), "-", round((true_f - oracle).mean(), 3)],
                ["Always adaptive", "-", round(true_a.mean(), 3), "-", round((true_a - oracle).mean(), 3)]]
    best_true_adapt = true_a < true_f
    for name in list(models())[1:]:
        choose_adapt = preds[("adaptive", name)] < preds[("fixed", name)]
        got = np.where(choose_adapt, true_a, true_f)
        acc = (choose_adapt == best_true_adapt).mean()
        sel_rows.append([f"ML selector ({name})", round(acc * 100, 1), round(got.mean(), 3),
                         int(choose_adapt.sum()), round((got - oracle).mean(), 3)])
    sel_rows.append(["Oracle (best mode known)", 100.0, round(oracle.mean(), 3),
                     int(best_true_adapt.sum()), 0.0])
    with open(os.path.join(DATA, "ml_mode_selection.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["strategy", "correct_choice_pct", "mean_avg_wait_s",
                    "scenarios_adaptive_chosen", "mean_regret_vs_oracle_s"])
        w.writerows(sel_rows)

    with open(os.path.join(DATA, "ml_test_predictions.csv"), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(names + ["fixed_actual_s", "fixed_pred_rf_s", "adaptive_actual_s", "adaptive_pred_rf_s"])
        for k, i in enumerate(te):
            w.writerow([round(v, 1) for v in X[i]] + [yf[i], round(preds[("fixed", "Random forest")][k], 3),
                                                        ya[i], round(preds[("adaptive", "Random forest")][k], 3)])

    # --- figures ---
    fig, axes = plt.subplots(1, 2, figsize=(9, 4))
    for ax, (target, y) in zip(axes, (("fixed", yf), ("adaptive", ya))):
        p = preds[(target, "Random forest")]
        ax.scatter(y[te], p, s=14, alpha=0.7)
        lo, hi = min(y[te].min(), p.min()), max(y[te].max(), p.max())
        ax.plot([lo, hi], [lo, hi], "k--", lw=1)
        r = [x for x in res if x[0] == target and x[1] == "Random forest"][0]
        ax.set_title(f"{target.capitalize()} mode (test MAE {r[4]} s, R² {r[6]})", fontsize=10)
        ax.set_xlabel("Simulated average wait (s)")
        ax.set_ylabel("Predicted average wait (s)")
    fig.suptitle("Random forest: predicted vs simulated average wait (test set)", fontsize=11)
    fig.tight_layout()

    fig, ax = plt.subplots(figsize=(7, 3.8))
    labels = [r[0].replace("ML selector (", "ML: ", 1)[:-1] if r[0].startswith("ML selector") else r[0].replace(" (best mode known)", "") for r in sel_rows]
    vals = [r[2] for r in sel_rows]
    bars = ax.barh(labels, vals, color=["#9aa5b1", "#9aa5b1", "#4c78a8", "#4c78a8", "#4c78a8", "#59a14f"])
    for b_, v in zip(bars, vals):
        ax.text(v + 0.1, b_.get_y() + b_.get_height() / 2, f"{v:.2f}", va="center", fontsize=9)
    ax.invert_yaxis()
    ax.set_xlim(0, max(vals) * 1.12)
    ax.set_xlabel("Mean average vehicle wait on test scenarios (s)")
    ax.set_title("Average wait by control-mode selection strategy", fontsize=11)
    fig.tight_layout()
    plt.show()


if __name__ == "__main__":
    main()
