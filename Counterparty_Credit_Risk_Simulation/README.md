Counterparty Credit Risk Simulation

Overview

This graduate-level Python project demonstrates an end-to-end counterparty credit risk (CCR) workflow for a synthetic derivatives portfolio. It combines regulatory exposure measurement with Monte Carlo simulation, collateral modeling, CVA, and wrong-way-risk analysis.

The full portfolio is used for netting and simplified SA-CCR calculations. The more detailed simulation is demonstrated on one netting set, CSA-005, to keep the project focused and computationally manageable.

Methodology

Data review and netting — examines trade characteristics, data quality, gross exposure, net exposure, and netting benefits.

Simplified SA-CCR — estimates replacement cost, potential future exposure, and exposure at default using supervisory factors, maturity factors, delta adjustments, and hedging-set aggregation.

Monte Carlo simulation — generates 5,000 correlated monthly paths for equity, FX, rates, commodity, and credit risk factors.

Trade revaluation — revalues trades along each path and calculates Expected Exposure (EE), Expected Positive Exposure (EPE), and Potential Future Exposure (PFE 95%/99%).

Collateral modeling — applies variation margin using a Threshold, Minimum Transfer Amount (MTA), and 10-business-day Margin Period of Risk (MPOR).

CVA and wrong-way risk — calculates collateralized CVA and tests whether exposure increases when the counterparty's credit proxy deteriorates.

Main outputs

The script produces:

Netting-set exposure and simplified SA-CCR EAD tables

Correlated market-risk-factor paths

Uncollateralized and collateralized EE/PFE profiles

CVA estimates and credit-spread sensitivity

Wrong-way-risk comparison and supporting diagnostics

Files

CCR-Project.py — complete analysis

CCR_Book_Simulation_v3.xlsx — synthetic derivatives portfolio

Keep both files in the same folder.

How to run

Install the required packages:

pip install pandas numpy scipy matplotlib openpyxl

Run:

python CCR-Project.py

The script saves intermediate simulation arrays and summary CSV files for later steps.

Assumptions and limitations

The valuation date is June 15, 2025.

Risk-factor dynamics, correlations, discount rates, credit spreads, and CSA terms are simplified scenario assumptions rather than market-calibrated inputs.

Product valuation uses practical approximations instead of full pricing and term-structure models.

The SA-CCR section is an educational approximation, not an official regulatory capital calculation.

The iTraxx-based wrong-way-risk relationship is a general market proxy, not a counterparty-specific calibration.

Data disclaimer

All trades, exposures, CSA terms, counterparty names, and agreement identifiers are fictional and created solely for educational purposes. The dataset does not represent any real institution or portfolio.