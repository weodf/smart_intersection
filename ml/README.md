# ML extension: control-mode selection

Section 5.7 of the report. Predicts the average vehicle wait of fixed-time and
adaptive control from the traffic demand, then chooses the mode with the
shorter predicted wait.

## Run (from the project root)

```
pip install -r ml/requirements.txt
python ml/ml_generate_dataset.py     # 1,000 scenarios x 2 modes with experiments/sim.py, about 6-10 min
python ml/ml_train_evaluate.py       # trains and evaluates the models, about 20 s
```

`ml_generate_dataset.py` uses only the standard library and the project's own
simulator. The dataset is deterministic: the same command always gives the same
`ml_dataset.csv`.

## Files

| File | Content |
|---|---|
| `ml_generate_dataset.py` | Generates `data/ml_dataset.csv` (1,000 random demand scenarios, 100-800 veh/h per approach, each run 30 simulated minutes in both modes) |
| `ml_train_evaluate.py` | Mean baseline, linear regression, random forest and MLP neural network; 80/20 train/test split with 5-fold cross-validation on the training set |
| `data/ml_dataset.csv` | Training data |
| `data/ml_model_results.csv` | Test MAE, RMSE and R2 of each model (report Table 5) |
| `data/ml_mode_selection.csv` | Mean wait and correct-choice rate of each selection strategy (report Table 5, Figure 6) |
| `data/ml_test_predictions.csv` | Actual and predicted wait for every test scenario |
| `figures/Figure_ML2_mode_selection.png` | Report Figure 6 |
| `figures/Figure_ML1_predicted_vs_actual.png` | Predicted vs simulated wait (not used in the report) |

## Results (200 test scenarios)

| Strategy | Correct mode | Mean wait (s) |
|---|---|---|
| Always fixed-time | - | 11.07 |
| Always adaptive | - | 9.77 |
| ML selector (linear regression / random forest / MLP) | 84-85% | 9.48-9.49 |
| Oracle (better mode known) | 100% | 9.39 |
