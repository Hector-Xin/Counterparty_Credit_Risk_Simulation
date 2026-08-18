# -*- coding: utf-8 -*-
"""
Created on Mon Aug 17 23:44:24 2026

@author: Diheng (Hector) Xin
"""


"""Counterparty Credit Risk Simulation with sythetic dataset"""
import pandas as pd
##view all columns 
pd.set_option('display.max_columns', None)
pd.set_option('display.width', None)
pd.set_option('display.max_colwidth', None)
import matplotlib.pyplot as plt
import numpy as np



##Load and take a first look
data = pd.read_excel("CCR_Book_Simulation_v3.xlsx", sheet_name="Derivatives Book")
data
data.shape #(rows, columns)
data.head() #first 5 rows 
data.tail() #last 5 rows
data.dtypes #data type of each columns
data.info() #types + non-null counts in one view

##check data quality
data.isnull().sum() #missing values per column
data.duplicated().sum() #exact duplicate rows
data['Trade_ID'].is_unique #sanity check on the trade ID

##Descriptive statistics
data.describe() #descriptive stats summary for each numerical column
data.describe(include = 'object') #descriptive stats for categorical variables
#for certain variable(s)
data['Notional_Amount'].describe() #descriptive stats for a selected variable
data['MTM (RC)'].describe()
data['MTM (RC)'].median() 
data['MTM (RC)'].skew()   #asymmetry — relevant since MTM can be positive or negative
data[['Notional_Amount','MTM (RC)']].corr()   # correlation between size and exposure

##Explore categorical variables
data['Counterparty'].value_counts()  # trade count per counterparty
data['Category'].value_counts()      # trade count per product type
data['Counterparty'].nunique()
data['Category'].nunique()

##Group and aggregate
#make certain 'rows' into 'columns - exposure (mtm/rc) per counterparty
data.groupby('Counterparty')['MTM (RC)'].agg(['sum','mean','count'])
# Notional and MTM by product category
data.groupby('Category')[['Notional_Amount','MTM (RC)']].sum()
# Which trades currently have positive exposure (in-the-money to you)?
data[data['MTM (RC)'] > 0]
#Total exposures/RC without netting
data[data['MTM (RC)'] > 0]['MTM (RC)'].sum()
#Total exposures/RC with netting
data['MTM (RC)'].sum()

##Look at the time dimension
data['Trade_Date'].min(), data['Trade_Date'].max()
data.set_index('Trade_Date').resample('YE')['Notional_Amount'].sum()   # notional booked per year
##Visual check
#distribution of MTM(RC)
data['MTM (RC)'].hist(bins=20)
plt.title('Distribution of MTM (Replacement Cost)')
plt.show()
#Net exposure by counterparty
data.groupby('Counterparty')['MTM (RC)'].sum().plot(kind='bar')
plt.title('Net Exposure by Counterparty')
plt.show()

VALUATION_DATE = pd.Timestamp("2025-06-15")

data["Maturity_Date"] = pd.to_datetime(
    data["Maturity_Date"], errors="raise"
)

# Save the complete dataset
all_data = data.copy()

# Parts 1 and 2 use only active trades
data = all_data[
    all_data["Maturity_Date"] > VALUATION_DATE
].copy()

print(
    f"Using {len(data)} live trades as of "
    f"{VALUATION_DATE.date()}; "
    f"{len(all_data) - len(data)} matured trades excluded."
)

"""PART 1: Netting Sets - Netting_Agreement_ID under ISDA MASTER AGREEMENT --------------------------------------"""
##General formula for SA-CCR EAD: 1.4*(RC+PFE), where RC (replacement cost) is the fully netting exposures for each Netting Agreement ID
##RC (Replacement Cost) — what it would cost you right now if the counterparty defaulted today. It's today's snapshot.
##PFE (Potential Future Exposure) — how much worse that exposure could realistically get before the trade matures, due to market moves. It's tomorrow's risk.

# Gross exposure: sum of positive MTM per netting set (ΣMax(MTM,0))
gross_exposure = (
    data[data['MTM (RC)'] > 0]
    .groupby(['Counterparty', 'Netting_Agreement_ID'])['MTM (RC)']
    .sum()
    .rename('Gross_Exposure')
)
gross_exposure

## Net exposure: sum of all MTM per netting set, floored at 0
net_mtm = data.groupby(['Counterparty', 'Netting_Agreement_ID'])['MTM (RC)'].sum().rename('Net_MTM')
net_mtm #total mtm per Netting Agreement ID - can be negative
net_exposure = net_mtm.clip(lower=0).rename('Net_Exposure') #Max(ΣMTM,0)
net_exposure #total mtm per Netting Agreement ID - floored at 0 

# Combine
netting_summary = pd.concat([gross_exposure, net_mtm, net_exposure], axis=1).fillna(0)
netting_summary['Netting_Benefit'] = netting_summary['Gross_Exposure'] - netting_summary['Net_Exposure']
netting_summary['Netting_Benefit_%'] = (
    netting_summary['Netting_Benefit'] / netting_summary['Gross_Exposure'].replace(0, pd.NA) * 100
).round(1)

netting_summary = netting_summary.sort_values('Netting_Benefit', ascending=False)
print(netting_summary.round(2))

"""PART 2: Simplified SA-CCR EAD (The Book) -------------------------------------------------------------------------------------------
This educational implementation simplifies interest-rate maturity buckets,
collateralized replacement cost and supervisory option treatment. Results
are estimates, not official regulatory capital calculations.
"""
##General formula for SA-CCR EAD: 1.4*(RC+PFE), where PFE = ΣNotionali x SFi x SDi x δi
#δ = ±Φ( (ln(spot/strike) + 0.5·σ²·T) / (σ·√T) ) >> delta add-on for option
  #δ applies to every trade - scale the notional >> directional expoure;
  #for non-option (linear), δ = +/- 1; for option, δ is between -1/+1
#SD (Supervisory Duraiton) =  [e(0.05*S)-e(0.05*E)]/0.05 - depends on asset class
  #Rates and Credit trades always use SD; FX, Equity, and commoditiy do not

"""delta for option and non-option"""  
from scipy.stats import norm

data["T"] = (
    data["Maturity_Date"] - VALUATION_DATE
).dt.days / 365.0

if (data["T"] <= 0).any():
    raise ValueError("Matured trades were not correctly excluded.")

#define call vs. put
data['Option_Type'] = np.where(data['Category'].str.contains('Put'), 'Put', 'Call')

#applying supervisory δ
def supervisory_delta(row):
    if not row['Is_Option']:
        return 1 if row['Direction'] == 'Long' else -1 #If not option, delta is either 1 for long and -1 for short
    
    S, K, sigma, T = row['Spot_Price'], row['Strike_Price'], row['Volatility'], row['T'] #if option, apply BS delta model
    d1 = (np.log(S / K) + 0.5 * sigma**2 * T)/(sigma * np.sqrt(T))
    
    base_delta = norm.cdf(d1) if row['Option_Type'] == 'Call' else -norm.cdf(-d1) #call: N(d1); put: N(-d1)
    return base_delta if row['Direction'] == 'Long' else -base_delta

data['Delta'] = data.apply(supervisory_delta, axis=1)
print(data[['Trade_ID','Category','Is_Option','Direction','Option_Type','Delta']].head(15))


"""SD for Credit and Rates asset class"""

data['SD'] = np.where(
    data['Asset_Class'].isin(['Rates', 'Credit']),
    (1 - np.exp(-0.05 * data['T'])) / 0.05,
    np.nan  # not used for FX, Equity, Commodity
)

print(data[['Trade_ID','Asset_Class','T','SD']].head(15))


"""SF (Supervisory Factor) per asset class"""
#Basel SA-CCR supervisory value
def get_supervisory_factor(row):
    if row['Asset_Class'] == 'Rates':
        return 0.005   # 0.50%
    elif row['Asset_Class'] == 'FX':
        return 0.04    # 4%
    elif row['Asset_Class'] == 'Credit':
        # Investment grade index vs high-yield index
        if row['Underlying_Risk_Factor'] in ['CDX.IG', 'ITRAXX.EU']:
            return 0.0038   # 0.38%
        else:  # CDX.HY
            return 0.0106   # 1.06%
    elif row['Asset_Class'] == 'Equity':
        return 0.20    # 20% (index-level, since we're treating equity as systematic-only)
    elif row['Asset_Class'] == 'Commodity':
        return 0.18    # 18% (energy category — oil/gas)

data['SF'] = data.apply(get_supervisory_factor, axis=1)

print(data[['Trade_ID','Asset_Class','Underlying_Risk_Factor','SF']].drop_duplicates())

"""MF (Maturity Factor)"""
#MA = Maturity Factor, adjustment factor for the remaining time horizon for which PFE could grow
  #margined = 1.5 x √(MPOR/1 Year), where MPOR is margin period of risk and floors at 10 day (with CSA)
  #unmargined = √min(M, 1) (without CSA)

MPOR_YEARS = 10 / 250  # 10 business days, standard assumption
data['M_floored'] = np.maximum(data['T'], MPOR_YEARS)
data['MF'] = np.where(
    data['CSA_Flag'],
    1.5 * np.sqrt(MPOR_YEARS), #for margined MF: CSA_Flag = TRUE
    np.sqrt(np.minimum(data['M_floored'], 1)) #for unmargined MF: CSA_Flag = FALSE
)
print(data[['Trade_ID','CSA_Flag','T', 'M_floored', 'MF']].head(10))



"""Aggregate Add-on: notional x SD x δ x SF x MF"""
#Effective notional Rates/Credit = notional_amount_i x SDi_i

data['Effective_Notional'] = np.where(
    data['Asset_Class'].isin(['Rates', 'Credit']),
    data['Notional_Amount'] * data['SD'],
    data['Notional_Amount'] #if false (not Rates/Credit assets), Effective Notional = Notional Amount
)

#Aggregate total Add-on
data['Trade_AddOn'] = data['Effective_Notional'] * data['Delta'] * data['SF'] * data['MF']
##PFE Table
print(data[['Trade_ID','Asset_Class','Notional_Amount','SD','Delta','SF', 'MF', 'Trade_AddOn']].head(15))

##AddOn_HedgingSet = √[ (ρ × ΣAddOn_k)² + (1−ρ²) × Σ(AddOn_k²) ] for credit, commodity, and equity derivatives


"""Aggregate Hedging Set"""
#AddOn_HedgingSet = √[ (ρ × ΣAddOn_k)² + (1−ρ²) × Σ(AddOn_k²) ] 
 #hedging set (correlation effect) for FX and rates are not applied here
 #Commodity correlation = 0.4, Equity (index) correlation = 0.8, Credit correlation = 0.8

# --- Basel supervisory correlation parameters (FX and Rates use a plain sum, no rho needed) ---
RHO = {'Commodity': 0.40, 'Equity': 0.80, 'Credit': 0.80}

def assign_hedging_set(row):
    """FX -> currency pair | Rates -> currency | Equity/Credit/Commodity -> whole asset class"""
    if row['Asset_Class'] == 'FX':
        return row['Underlying_Risk_Factor']
    elif row['Asset_Class'] == 'Rates':
        return row['Currency']
    else:
        return row['Asset_Class']

def aggregate_hedging_set(group, asset_class):
    """Aggregate trade add-ons within one simplified hedging set."""

    if asset_class in {"FX", "Rates"}:
        return abs(group["Trade_AddOn"].sum())

    rho = RHO[asset_class]

    entity_addons = (
        group.groupby("Underlying_Risk_Factor")["Trade_AddOn"]
        .sum()
    )

    systematic_term = (rho * entity_addons.sum()) ** 2
    idiosyncratic_term = (
        (1 - rho**2) * (entity_addons**2).sum()
    )

    return np.sqrt(systematic_term + idiosyncratic_term)

# --- Run ---
data['Hedging_Set'] = data.apply(assign_hedging_set, axis=1)

results = [
    {
        'Counterparty': cpty,
        'Netting_Agreement_ID': netting_id,
        'Asset_Class': asset_class,
        'Hedging_Set': hedging_set,
        'HedgingSet_AddOn': aggregate_hedging_set(group, asset_class)
    }
    for (cpty, netting_id, asset_class, hedging_set), group in data.groupby(
        ['Counterparty', 'Netting_Agreement_ID', 'Asset_Class', 'Hedging_Set']
    )
]

hedging_set_addons = pd.DataFrame(results)
if (hedging_set_addons["HedgingSet_AddOn"] < 0).any():
    raise ValueError("SA-CCR hedging-set add-ons cannot be negative.")
print(hedging_set_addons.head(20))
##PFE grouped by based on hedging set with asset class supervisory correlations

"""Multiplier for Netting Set - recognition of excess collateral/negative MTM"""
#(COLLATERAL is temporarily ignored and will be modeled in PART 4)

#Apply mulpliers to the total hedge set add-ons per Netting Agreement ID 

# a. sum hedging-set add-ons up to the netting-set level
total_addon = (
    hedging_set_addons.groupby(['Counterparty', 'Netting_Agreement_ID'])['HedgingSet_AddOn']
    .sum()
    .rename('Total_AddOn')
) ##gives total add-ons (hedging sets) per Netting Agreement ID

# b. bring in RC and net MTM from Step 1's netting_summary
ead_table = netting_summary[['Net_MTM', 'Net_Exposure']].rename(columns={'Net_Exposure': 'RC'})
ead_table = ead_table.join(total_addon)
ead_table #RC + PFE (unmultiplied)

# c. PFE multiplier = min(1, 5% + 95% × exp(V / (2 × 95% × ΣAddOn)))
 #Floor is 5% based on Basel prescription
ead_table['Multiplier'] = np.minimum(
    1,
    0.05 + 0.95 * np.exp(ead_table['Net_MTM'] / (2 * 0.95 * ead_table['Total_AddOn']))
) #multiply each PFE by the multiplier 

# d. PFE and final EAD
ead_table['PFE'] = ead_table['Multiplier'] * ead_table['Total_AddOn']
ead_table['EAD'] = 1.4 * (ead_table['RC'] + ead_table['PFE'])

ead_table = ead_table.sort_values('EAD', ascending=False)
print(ead_table.round(2)) ##SA-CCR EAD



"""PART 3: Monte Carlo Simulations - PFE, EPE, EE - and Revaluation (CSA-005)  ------------------------------------------------------"""
#Simulated exposure profiles — Monte Carlo risk factor paths, trade revaluation over time, PFE (95th/99th pct), EPE, EE

"""3a/3b: Time grid + correlated Monte Carlo risk-factor path simulation ------------------------"""

"""
Step 3a/3b: Correlated Monte Carlo Risk-Factor Simulation

This section simulates monthly risk-factor paths for the live trades in CSA-005. 
Trade revaluation and exposure calculations are completed in later steps.

The valuation date is 2025-06-15, based on the synthetic book’s activity. 
Equity and FX follow zero-drift Geometric Brownian Motion. Rates, commodities and credit use a log-space mean-reverting process 
to avoid unrealistic long-term movements. Risk factors are correlated, 
including a negative SPX–ITRAXX relationship for the later wrong-way-risk analysis.

The monthly time grid begins exactly on the valuation date and runs to the longest trade maturity. 
Volatility is averaged across live trades that share the same underlying risk factor.

The code validates required fields, data types, positive spot and volatility values, consistent risk-factor classifications, 
assigned mean-reversion parameters and a positive-definite correlation matrix.

Limitations: Model parameters and correlations are stylized rather than market-calibrated. 
Volatility is recorded by trade rather than risk factor, and inconsistent CSA terms are handled separately in the collateral section.
"""


data = all_data.copy()

# ----------------------------------------------------------------------
# 0. Config
# ----------------------------------------------------------------------
VALUATION_DATE = pd.Timestamp("2025-06-15")
NETTING_SET = "CSA-005"
N_PATHS = 5000
RANDOM_SEED = 42
DEFAULT_CROSS_CORR = 0.10  # generic small positive co-movement for unrelated pairs

KAPPA_BY_ASSET_CLASS = {
    "Rates": 1.0,      # ~0.7yr half-life - short rates revert quickly
    "Commodity": 0.3,  # ~2.3yr half-life - reverts toward cost of production
    "Credit": 0.3,     # ~2.3yr half-life - spreads drift back to a normal range
    "Equity": 0.0,     # pure GBM/random walk - standard convention
    "FX": 0.0,         # pure GBM/random walk - standard convention
}

# ----------------------------------------------------------------------
# 1. Load & scope to the prototype netting set
#    (reuses `data` if it's already in the namespace from Steps 1-2,
#    otherwise loads it fresh)
# ----------------------------------------------------------------------

data.columns = data.columns.astype(str).str.strip()


required_columns = {
    "Trade_ID", "Netting_Agreement_ID", "Maturity_Date",
    "Underlying_Risk_Factor", "Asset_Class", "Spot_Price", "Volatility",
}
missing_columns = required_columns - set(data.columns)
if missing_columns:
    raise ValueError(f"Missing required columns: {sorted(missing_columns)}")

netting_set = data[data["Netting_Agreement_ID"] == NETTING_SET].copy()
if netting_set.empty:
    raise ValueError(f"No trades found for netting set {NETTING_SET}.")

netting_set["Maturity_Date"] = pd.to_datetime(netting_set["Maturity_Date"], errors="raise")
netting_set["Spot_Price"] = pd.to_numeric(netting_set["Spot_Price"], errors="raise")
netting_set["Volatility"] = pd.to_numeric(netting_set["Volatility"], errors="raise")

important_columns = ["Trade_ID", "Maturity_Date", "Underlying_Risk_Factor",
                      "Asset_Class", "Spot_Price", "Volatility"]
if netting_set[important_columns].isna().any().any():
    raise ValueError(f"{NETTING_SET} contains missing required values.")

netting_set["Live"] = netting_set["Maturity_Date"] >= VALUATION_DATE
live_trades = netting_set[netting_set["Live"]].copy()
if live_trades.empty:
    raise ValueError(f"{NETTING_SET} has no live trades as of {VALUATION_DATE.date()}.")

print(f"{NETTING_SET}: {len(netting_set)} trades total, "
      f"{len(live_trades)} live as of {VALUATION_DATE.date()}, "
      f"{len(netting_set) - len(live_trades)} already matured (excluded)")

# ----------------------------------------------------------------------
# 2. One starting level + one volatility per underlying risk factor
# ----------------------------------------------------------------------
asset_class_counts = live_trades.groupby("Underlying_Risk_Factor")["Asset_Class"].nunique()
if (asset_class_counts > 1).any():
    invalid = asset_class_counts[asset_class_counts > 1].index.tolist()
    raise ValueError(f"Risk factors assigned to multiple asset classes: {invalid}")

spot_counts = live_trades.groupby("Underlying_Risk_Factor")["Spot_Price"].nunique()
if (spot_counts > 1).any():
    invalid = spot_counts[spot_counts > 1].index.tolist()
    raise ValueError(f"Risk factors with inconsistent starting levels: {invalid}")

risk_factors = (
    live_trades.groupby("Underlying_Risk_Factor")
    .agg(
        Asset_Class=("Asset_Class", "first"),
        Spot=("Spot_Price", "first"),
        Vol=("Volatility", "mean"),
        N_Trades=("Trade_ID", "count"),
    )
    .sort_values("Asset_Class")
)

if (risk_factors["Spot"] <= 0).any():
    raise ValueError("Log-space simulation requires positive starting levels.")
if (risk_factors["Vol"] <= 0).any():
    raise ValueError("Simulation requires positive volatilities.")

print("\nRisk factors in scope:")
print(risk_factors.round(3))

factors = risk_factors.index.tolist()
n_factors = len(factors)

# ----------------------------------------------------------------------
# 3. Time grid: monthly from the valuation date to the netting set's
#    longest live maturity, anchored to the valuation date itself
# ----------------------------------------------------------------------
horizon_end = pd.Timestamp(live_trades["Maturity_Date"].max())
if horizon_end <= VALUATION_DATE:
    raise ValueError("The netting set has no future simulation period.")

# DateOffset(months=1) (not the "MS" alias) keeps the valuation date as
# the first grid point instead of snapping to the next calendar month-start.
time_grid = pd.date_range(start=VALUATION_DATE, end=horizon_end, freq=pd.DateOffset(months=1))
if time_grid[-1] < horizon_end:
    time_grid = time_grid.append(pd.DatetimeIndex([horizon_end]))
time_grid = time_grid.drop_duplicates().sort_values()

t_years = np.array([(d - VALUATION_DATE).days / 365.0 for d in time_grid])
assert time_grid[0] == VALUATION_DATE
assert t_years[0] == 0.0

n_steps = len(t_years) - 1

print(f"\nTime grid: {len(time_grid)} monitoring dates, "
      f"{VALUATION_DATE.date()} -> {horizon_end.date()} "
      f"({t_years[-1]:.2f} years, monthly steps)")

# ----------------------------------------------------------------------
# 4. Correlation matrix - stylized, not calibrated to market data.
#    Every off-diagonal choice is documented; nothing here is "real" data,
#    consistent with the rest of the project's synthetic-but-defensible
#    approach.
# ----------------------------------------------------------------------
corr = np.full((n_factors, n_factors), DEFAULT_CROSS_CORR)
np.fill_diagonal(corr, 1.0)


def set_corr(f1, f2, val):
    if not -1.0 <= val <= 1.0:
        raise ValueError("Correlation must be between -1 and 1.")
    i, j = factors.index(f1), factors.index(f2)
    corr[i, j] = corr[j, i] = val


# Same asset class -> modest positive co-movement
if "WTI_CRUDE" in factors and "GOLD" in factors:
    set_corr("WTI_CRUDE", "GOLD", 0.30)

# Classic wrong-way-risk relationship: equity index down <-> credit spread
# index up. This becomes true WWR once the credit factor is tied to
# Millennium's own PD/credit spread in Step 6.
if "SPX" in factors and "ITRAXX.EU" in factors:
    set_corr("SPX", "ITRAXX.EU", -0.50)

corr_df = pd.DataFrame(corr, index=factors, columns=factors)

minimum_eigenvalue = np.linalg.eigvalsh(corr).min()
if minimum_eigenvalue <= 0:
    raise ValueError(
        f"Correlation matrix is not positive definite. "
        f"Minimum eigenvalue: {minimum_eigenvalue:.6f}"
    )

print("\nTarget correlation matrix:")
print(corr_df.round(2))

# ----------------------------------------------------------------------
# 4b. Mean reversion speed per factor, by asset class. kappa=0 keeps a
#     factor as pure GBM; kappa>0 makes it mean-revert toward its own
#     starting level (in log-space, so levels stay positive).
# ----------------------------------------------------------------------
kappa_series = risk_factors.loc[factors, "Asset_Class"].map(KAPPA_BY_ASSET_CLASS)
if kappa_series.isna().any():
    unknown_classes = risk_factors.loc[kappa_series.isna(), "Asset_Class"].unique().tolist()
    raise ValueError(f"Missing kappa for asset classes: {unknown_classes}")

kappas = kappa_series.to_numpy(dtype=float)
theta = np.log(risk_factors.loc[factors, "Spot"].to_numpy(dtype=float))

print("\nMean reversion speed (kappa) by factor:")
print(risk_factors.loc[factors].assign(Kappa=kappas)[["Asset_Class", "Kappa"]])

# ----------------------------------------------------------------------
# 5. Correlated path simulation via Cholesky decomposition.
#    GBM for kappa=0 factors, exact Ornstein-Uhlenbeck transition
#    (in log-space) for kappa>0 factors - no step-size bias either way.
# ----------------------------------------------------------------------
L = np.linalg.cholesky(corr)

log_paths = np.zeros((N_PATHS, n_steps + 1, n_factors))
log_paths[:, 0, :] = np.log(risk_factors.loc[factors, "Spot"].values)

sigmas = risk_factors.loc[factors, "Vol"].to_numpy(dtype=float)
is_ou = kappas > 0
safe_kappas = np.where(is_ou, kappas, 1.0)  # avoid divide-by-zero for GBM factors

simulated_shocks = np.zeros((N_PATHS, n_steps, n_factors))  # kept for the shock-level sanity check

rng = np.random.default_rng(RANDOM_SEED)
for step in range(n_steps):
    dt = t_years[step + 1] - t_years[step]
    if dt <= 0:
        raise ValueError("Simulation time steps must be positive.")

    Z = rng.standard_normal((N_PATHS, n_factors))
    Z_corr = Z @ L.T
    simulated_shocks[:, step, :] = Z_corr
    X = log_paths[:, step, :]

    # GBM branch: driftless in level space -> -0.5*sigma^2*dt in log space
    gbm_mean = X - 0.5 * sigmas ** 2 * dt
    gbm_std = sigmas * np.sqrt(dt)

    # OU branch: exact transition, reverts toward theta at speed kappa
    ou_mean = theta + (X - theta) * np.exp(-safe_kappas * dt)
    ou_var = (sigmas ** 2) / (2 * safe_kappas) * (1 - np.exp(-2 * safe_kappas * dt))
    ou_std = np.sqrt(ou_var)

    mean = np.where(is_ou, ou_mean, gbm_mean)
    std = np.where(is_ou, ou_std, gbm_std)
    log_paths[:, step + 1, :] = mean + std * Z_corr

paths = np.exp(log_paths)

# ----------------------------------------------------------------------
# 6. Sanity checks - both levels, belt-and-suspenders:
#    (a) correlation of the raw Z_corr shocks -> confirms the Cholesky
#        wiring is correct (should match target almost exactly, this is
#        the weaker but foundational check)
#    (b) correlation of the actual simulated log-returns -> confirms the
#        compounded process (mean reversion included) doesn't distort the
#        correlation structure in the output that downstream steps use
# ----------------------------------------------------------------------
if n_factors > 1:
    realized_shock_corr = pd.DataFrame(
        np.corrcoef(simulated_shocks.reshape(-1, n_factors), rowvar=False),
        index=factors, columns=factors,
    )
    print("\n(a) Realized vs target SHOCK correlation (difference, should be ~0):")
    print((realized_shock_corr - corr_df).round(2))

    log_rets = np.diff(np.log(paths), axis=1)
    realized_return_corr = pd.DataFrame(
        np.corrcoef(log_rets.reshape(-1, n_factors), rowvar=False),
        index=factors, columns=factors,
    )
    print("\n(b) Realized vs target LOG-RETURN correlation (difference, should be ~0):")
    print((realized_return_corr - corr_df).round(2))
else:
    print("\nOnly one risk factor: correlation check is not applicable.")

final = pd.DataFrame(paths[:, -1, :], columns=factors)
print(f"\nSimulated levels at final time step ({horizon_end.date()}), across {N_PATHS} paths:")
print(final.describe().T[["mean", "std", "min", "max"]].round(2))

# ----------------------------------------------------------------------
# 7. Persist for the next step (trade revaluation)
# ----------------------------------------------------------------------
np.save("csa005_paths.npy", paths)          # shape (N_PATHS, n_steps+1, n_factors)
risk_factors.to_csv("csa005_risk_factors.csv")
pd.Series(time_grid, name="date").to_csv("csa005_time_grid.csv", index=False)

print("\nSaved: csa005_paths.npy, csa005_risk_factors.csv, csa005_time_grid.csv")


"""3c: Trade revluation + simulated PFE/EE--------------------------------------"""


"""
CCR Simulation Project - Step 3c
Trade revaluation along simulated paths -> EE / PFE(95/99) / EPE profiles.

"""
"""
Step 3c: Trade Revaluation and Exposure Profiles

This section revalues each CSA-005 trade across the simulated paths and calculates EE, EPE, 
and PFE at the 95% and 99% confidence levels.

Revaluation methods:

* Equity, FX and commodity trades use a forward-style valuation based on changes in the simulated underlying level.
* Rate swaps and CDS use an annuity-based approach, where sensitivity declines with the trade’s remaining maturity.
* The payer swaption is valued as a Black-76-style call. Its scale is calibrated to reproduce the current reported MTM.

Every trade must reproduce its known MTM at the valuation date. After its maturity date, the trade’s value becomes zero. 
Discounting is not applied in this step.

The annuity approach is more realistic than applying percentage price changes to rates and credit spreads, 
but it remains simplified. Remaining maturity is used as a proxy for a discounted annuity, 
and rate/spread units are inferred from their size because the dataset does not provide explicit units. 
The swaption uses its trade-specific volatility.
"""


import numpy as np
import pandas as pd
from scipy.stats import norm

VALUATION_DATE = pd.Timestamp("2025-06-15")
NETTING_SET = "CSA-005"

# ----------------------------------------------------------------------
# 1. Load Step 3a/3b outputs + reload live trade detail
# ----------------------------------------------------------------------
paths = np.load("csa005_paths.npy")                      # (N_PATHS, n_steps+1, n_factors)
risk_factors = pd.read_csv("csa005_risk_factors.csv", index_col=0)
risk_factors.index = risk_factors.index.astype(str)
time_grid = pd.to_datetime(pd.read_csv("csa005_time_grid.csv")["date"])

factors = risk_factors.index.tolist()
N_PATHS, n_dates, n_factors = paths.shape

if n_dates != len(time_grid):
    raise ValueError("Path dates do not match csa005_time_grid.csv.")
if n_factors != len(factors):
    raise ValueError("Path factors do not match csa005_risk_factors.csv.")
if time_grid.iloc[0] != VALUATION_DATE:
    raise ValueError("The first simulation date must equal VALUATION_DATE.")
if not np.isfinite(paths).all() or (paths <= 0).any():
    raise ValueError("Simulated risk-factor paths must be finite and positive.")

starting_levels = risk_factors.loc[factors, "Spot"].to_numpy(dtype=float)
if not np.allclose(paths[:, 0, :], starting_levels[np.newaxis, :], rtol=1e-10, atol=1e-12):
    raise ValueError("Path values at t=0 do not match the saved starting levels.")

t_years = np.array([(d - VALUATION_DATE).days / 365.0 for d in time_grid])

data = pd.read_excel("CCR_Book_Simulation_v3.xlsx")
data.columns = data.columns.astype(str).str.strip()
data = data.loc[:, ~data.columns.str.startswith("Unnamed")]

required_columns = {
    "Trade_ID", "Netting_Agreement_ID", "Maturity_Date", "Underlying_Risk_Factor",
    "Asset_Class", "Direction", "Is_Option", "MTM (RC)", "Notional_Amount",
    "Strike_Price", "Volatility",
}
missing_columns = required_columns - set(data.columns)
if missing_columns:
    raise ValueError(f"Missing required columns: {sorted(missing_columns)}")

netting_set = data[data["Netting_Agreement_ID"] == NETTING_SET].copy()
if netting_set.empty:
    raise ValueError(f"No trades found for {NETTING_SET}.")

netting_set["Maturity_Date"] = pd.to_datetime(netting_set["Maturity_Date"], errors="raise")
netting_set["Underlying_Risk_Factor"] = netting_set["Underlying_Risk_Factor"].astype(str)
for col in ["MTM (RC)", "Notional_Amount", "Strike_Price", "Volatility"]:
    netting_set[col] = pd.to_numeric(netting_set[col], errors="coerce")

live_trades = netting_set[netting_set["Maturity_Date"] >= VALUATION_DATE].copy()
if live_trades.empty:
    raise ValueError(f"{NETTING_SET} has no live trades as of {VALUATION_DATE.date()}.")

invalid_directions = set(live_trades["Direction"].dropna()) - {"Long", "Short"}
if invalid_directions or live_trades["Direction"].isna().any():
    raise ValueError(f"Direction must be 'Long' or 'Short'. Invalid values: {sorted(invalid_directions)}")
live_trades["Sign"] = live_trades["Direction"].map({"Long": 1.0, "Short": -1.0})

live_trades["Is_Option"] = live_trades["Is_Option"].astype(bool)

if live_trades[["MTM (RC)", "Notional_Amount"]].isna().any().any():
    raise ValueError(f"{NETTING_SET} contains missing MTM or notional values.")
if (live_trades["Notional_Amount"] <= 0).any():
    raise ValueError("Notional_Amount must be positive.")

missing_factors = sorted(set(live_trades["Underlying_Risk_Factor"]) - set(factors))
if missing_factors:
    raise ValueError(f"Trades reference risk factors not in the simulation: {missing_factors}")

print(f"Revaluing {len(live_trades)} live trades in {NETTING_SET} "
      f"across {N_PATHS} paths x {n_dates} dates")

# ----------------------------------------------------------------------
# 2. Pricing helpers
# ----------------------------------------------------------------------
def black76_call(forward, strike, sigma, tau):
    """Undiscounted Black-76-style call. tau<=0 -> intrinsic value."""
    forward = np.asarray(forward, dtype=float)
    intrinsic = np.maximum(forward - strike, 0.0)
    if tau <= 0:
        return intrinsic
    vol_time = sigma * np.sqrt(tau)
    d1 = (np.log(forward / strike) + 0.5 * sigma ** 2 * tau) / vol_time
    d2 = d1 - vol_time
    return forward * norm.cdf(d1) - strike * norm.cdf(d2)


def rate_credit_scale(asset_class, starting_level):
    """
    Convert a raw risk-factor level into decimal form for the annuity
    formula. This is a magnitude heuristic, not an explicit units field -
    flagged as a caveat above.
    """
    if asset_class == "Rates":
        return 0.01 if abs(starting_level) > 1.0 else 1.0
    if asset_class == "Credit":
        if abs(starting_level) > 10.0:
            return 0.0001   # e.g. 55 -> 55bps -> 0.0055
        if abs(starting_level) >= 1.0:
            return 0.01     # e.g. 5.5 -> 5.5% -> 0.055
        return 1.0          # already decimal
    return 1.0


def remaining_years(maturity_date):
    """Remaining maturity at every grid date, floored at zero."""
    return np.maximum(np.array([(maturity_date - d).days / 365.0 for d in time_grid]), 0.0)

# ----------------------------------------------------------------------
# 3. Revalue every trade, every date, every path
# ----------------------------------------------------------------------
trade_mtm = np.zeros((N_PATHS, n_dates, len(live_trades)))
method_used = []

for i, (_, trade) in enumerate(live_trades.iterrows()):
    asset_class = trade["Asset_Class"]
    sign = trade["Sign"]
    notional = float(trade["Notional_Amount"])
    mtm0 = float(trade["MTM (RC)"])

    factor_idx = factors.index(trade["Underlying_Risk_Factor"])
    S0 = float(risk_factors.loc[trade["Underlying_Risk_Factor"], "Spot"])
    S_path = paths[:, :, factor_idx]                      # (N_PATHS, n_dates)
    alive = (time_grid <= trade["Maturity_Date"]).to_numpy()
    years_left = remaining_years(trade["Maturity_Date"])

    if trade["Is_Option"]:
        # Swaption: undiscounted Black-76, scale calibrated to MTM_RC.
        K = float(trade["Strike_Price"])
        sigma = float(trade["Volatility"])
        tau0 = years_left[0]
        price0 = black76_call(S0, K, sigma, tau0)
        k = mtm0 / (sign * price0)

        mtm = np.zeros_like(S_path)
        for t_idx, tau in enumerate(years_left):
            if tau < 0:
                continue
            mtm[:, t_idx] = sign * k * black76_call(S_path[:, t_idx], K, sigma, tau)

        method = "Black-76-style option"

    elif asset_class in {"Rates", "Credit"}:
        # Swap / CDS: annuity-scaled sensitivity to the rate/spread level,
        # not a percentage move of the level - see docstring.
        scale = rate_credit_scale(asset_class, S0)
        level0 = S0 * scale
        level_path = S_path * scale
        annuity0 = years_left[0]
        annuity_path = years_left[np.newaxis, :]

        contract_level = level0 - mtm0 / (sign * notional * annuity0)
        mtm = sign * notional * annuity_path * (level_path - contract_level)

        method = "Annuity-based swap" if asset_class == "Rates" else "Annuity-based CDS"

    else:
        # Equity / FX / Commodity: forward position, contract price implied
        # from MTM_RC - see docstring, algebraically the same as a plain
        # notional x percentage-move formula.
        units = notional / S0
        contract_price = S0 - mtm0 / (sign * units)
        mtm = sign * units * (S_path - contract_price)

        method = "Forward-style linear"

    mtm = np.where(alive[np.newaxis, :], mtm, 0.0)
    trade_mtm[:, :, i] = mtm
    method_used.append(method)

if not np.isfinite(trade_mtm).all():
    raise ValueError("Trade revaluation produced NaN or infinite values.")

# ----------------------------------------------------------------------
# 4. Sanity check: every trade's t=0 simulated MTM must equal its known
#    MTM_RC, regardless of which valuation method it used
# ----------------------------------------------------------------------
t0_check = pd.DataFrame({
    "Trade_ID": live_trades["Trade_ID"].values,
    "Asset_Class": live_trades["Asset_Class"].values,
    "Method": method_used,
    "MTM_RC": live_trades["MTM (RC)"].values,
    "Simulated_t0": trade_mtm[0, 0, :],
})
t0_check["Match"] = np.isclose(t0_check["MTM_RC"], t0_check["Simulated_t0"], atol=1.0)
print("\nt=0 reproduction check (every trade should match its known MTM_RC):")
print(t0_check.to_string(index=False))
assert t0_check["Match"].all(), "t=0 revaluation does not reproduce known MTM_RC"

# ----------------------------------------------------------------------
# 5. Net within the netting set, floor at zero for exposure
# ----------------------------------------------------------------------
netted_mtm = trade_mtm.sum(axis=2)          # (N_PATHS, n_dates)
exposure = np.maximum(netted_mtm, 0.0)      # (N_PATHS, n_dates)

# ----------------------------------------------------------------------
# 6. EE / PFE(95/99) / EPE profiles
# ----------------------------------------------------------------------
EE = exposure.mean(axis=0)
PFE_95 = np.percentile(exposure, 95, axis=0)
PFE_99 = np.percentile(exposure, 99, axis=0)
trapezoid = getattr(np, "trapezoid", None) or np.trapz  # numpy >=2.0 renamed trapz
EPE = trapezoid(EE, t_years) / t_years[-1]  # time-weighted average of EE

profile = pd.DataFrame({
    "Date": time_grid.values,
    "t_years": t_years,
    "EE": EE,
    "PFE_95": PFE_95,
    "PFE_99": PFE_99,
})

print(f"\nEPE (time-weighted average of EE over {t_years[-1]:.2f}y): {EPE:,.0f}")
print("\nExposure profile at selected tenors:")
for cp in [0.0, 1.0, 2.0, 3.0, 5.0, 8.0]:
    idx = (np.abs(t_years - cp)).argmin()
    row = profile.iloc[idx]
    print(f"  t={row['t_years']:.2f}y ({row['Date'].date()}): "
          f"EE={row['EE']:,.0f}  PFE_95={row['PFE_95']:,.0f}  PFE_99={row['PFE_99']:,.0f}")

# ----------------------------------------------------------------------
# 7. Persist for later steps (Step 4 collateral, Step 5 CVA)
# ----------------------------------------------------------------------
profile.to_csv("csa005_exposure_profile.csv", index=False)
np.save("csa005_netted_mtm.npy", netted_mtm)
np.save("csa005_exposure.npy", exposure)
print("\nSaved: csa005_exposure_profile.csv, csa005_netted_mtm.npy, csa005_exposure.npy")



"""PART 4: Collateral/CSA Modeling (CSA-005)---------------------------------------------------------------------------"""
#Collateral Adjusted PFE

"""
Scope: CSA-005 only, per the project's permanent Step 3-6 scoping decision
(full-book coverage stops after Step 2's SA-CCR EAD).


Step 4: Collateral and CSA Modeling

This section applies collateral to CSA-005 using a $500,000 Threshold,
 $250,000 MTA, and 10-business-day MPOR.

CSA terms are inconsistently recorded by trade, although they should apply to 
the entire netting set. Therefore, the Threshold and MTA are treated as 
scenario assumptions based on the most common values among collateralized trades. 
All amounts are assumed to use the same currency.

Variation margin is simulated on a daily business-day grid. Required collateral
equals exposure above the Threshold, but collateral moves only when the 
difference between required and held collateral reaches the MTA. 
At each monthly reporting date, available collateral is taken from 10 business 
days earlier to reflect the MPOR closeout delay.

Because CSA-005 represents an existing relationship, starting collateral is 
assumed to equal the initial required amount. Collateral is modeled one-way 
and only reduces the bank’s positive exposure to the counterparty.

Limitations: Monthly exposures are interpolated onto the daily grid. 
The model excludes initial margin, haircuts, collateral interest, 
disputes and collateral-value changes. Exposure may exceed the Threshold 
between margin calls because of the MTA and MPOR.
"""


import numpy as np
import pandas as pd

VALUATION_DATE = pd.Timestamp("2025-06-15")
NETTING_SET = "CSA-005"

THRESHOLD = 500_000.0
MTA = 250_000.0
BUSINESS_DAYS_PER_YEAR = 250
MPOR_BUSINESS_DAYS = 10
MPOR_YEARS = MPOR_BUSINESS_DAYS / BUSINESS_DAYS_PER_YEAR

# ----------------------------------------------------------------------
# 1. Load Step 3c outputs
# ----------------------------------------------------------------------
exposure = np.load("csa005_exposure.npy")               # (N_PATHS, n_dates)
profile = pd.read_csv("csa005_exposure_profile.csv")

required_columns = {"Date", "t_years", "EE", "PFE_95", "PFE_99"}
missing_columns = required_columns - set(profile.columns)
if missing_columns:
    raise ValueError(f"csa005_exposure_profile.csv is missing: {sorted(missing_columns)}")

time_grid = pd.to_datetime(profile["Date"], errors="raise")
t_years = pd.to_numeric(profile["t_years"], errors="raise").to_numpy()

if exposure.ndim != 2:
    raise ValueError("csa005_exposure.npy must be a two-dimensional array.")
if exposure.shape[1] != len(t_years):
    raise ValueError("csa005_exposure.npy does not match csa005_exposure_profile.csv's grid.")
if not np.isfinite(exposure).all() or (exposure < 0).any():
    raise ValueError("Exposure must be finite and non-negative.")
if time_grid.iloc[0] != VALUATION_DATE or not np.isclose(t_years[0], 0.0):
    raise ValueError("The first profile date must equal VALUATION_DATE (t=0).")
if np.any(np.diff(t_years) <= 0):
    raise ValueError("The exposure time grid must be strictly increasing.")
if THRESHOLD < 0 or MTA <= 0:
    raise ValueError("Threshold must be non-negative and MTA must be positive.")

N_PATHS, n_dates = exposure.shape
horizon = t_years[-1]

print(f"Applying collateral to {NETTING_SET}: Threshold=${THRESHOLD:,.0f}, "
      f"MTA=${MTA:,.0f}, MPOR={MPOR_BUSINESS_DAYS} business days")

# ----------------------------------------------------------------------
# 2. Build the daily (business-day) grid and locate each monthly
#    reporting date's MPOR lookback point on it
# ----------------------------------------------------------------------
business_step = 1.0 / BUSINESS_DAYS_PER_YEAR
business_grid = np.arange(0.0, horizon + business_step / 2, business_step)
business_grid = business_grid[business_grid <= horizon]
if business_grid[-1] < horizon:
    business_grid = np.append(business_grid, horizon)

lookback_t = np.maximum(t_years - MPOR_YEARS, 0.0)
lookback_indices = np.searchsorted(business_grid, lookback_t, side="right") - 1
lookback_indices = np.clip(lookback_indices, 0, len(business_grid) - 1)

# Multiple reporting dates can share the same business-day lookback point.
capture_map = {}
for report_idx, business_idx in enumerate(lookback_indices):
    capture_map.setdefault(int(business_idx), []).append(report_idx)

# ----------------------------------------------------------------------
# 3. Daily variation-margin simulation with Threshold and MTA
# ----------------------------------------------------------------------
# Existing CSA, existing book - start from the day-one required level
# rather than assuming zero collateral history (see docstring).
collateral_balance = np.maximum(exposure[:, 0] - THRESHOLD, 0.0)

collateral_held = np.full_like(exposure, np.nan)  # collateral available at each report date
for report_idx in capture_map.get(0, []):
    collateral_held[:, report_idx] = collateral_balance

for business_idx in range(1, len(business_grid)):
    current_t = business_grid[business_idx]

    # Linearly interpolate exposure between the surrounding monthly points.
    right_idx = np.searchsorted(t_years, current_t, side="right")
    if right_idx >= n_dates:
        exposure_today = exposure[:, -1]
    else:
        left_idx = right_idx - 1
        weight = (current_t - t_years[left_idx]) / (t_years[right_idx] - t_years[left_idx])
        exposure_today = exposure[:, left_idx] * (1.0 - weight) + exposure[:, right_idx] * weight

    collateral_required = np.maximum(exposure_today - THRESHOLD, 0.0)
    transfer_triggered = np.abs(collateral_required - collateral_balance) >= MTA
    collateral_balance = np.where(transfer_triggered, collateral_required, collateral_balance)

    for report_idx in capture_map.get(business_idx, []):
        collateral_held[:, report_idx] = collateral_balance

if not np.isfinite(collateral_held).all():
    raise ValueError("Some reporting dates did not receive a collateral balance.")
if (collateral_held < 0).any():
    raise ValueError("Held collateral cannot be negative in this one-way model.")

# ----------------------------------------------------------------------
# 4. Collateralized exposure
# ----------------------------------------------------------------------
exposure_collateralized = np.maximum(exposure - collateral_held, 0.0)

# ----------------------------------------------------------------------
# 5. EE / PFE(95/99) / EPE profiles, collateralized vs. uncollateralized
# ----------------------------------------------------------------------
trapezoid = getattr(np, "trapezoid", None) or np.trapz

EE_uncollat = exposure.mean(axis=0)
PFE_95_uncollat = np.percentile(exposure, 95, axis=0)
PFE_99_uncollat = np.percentile(exposure, 99, axis=0)
EPE_uncollat = trapezoid(EE_uncollat, t_years) / horizon

EE_collat = exposure_collateralized.mean(axis=0)
PFE_95_collat = np.percentile(exposure_collateralized, 95, axis=0)
PFE_99_collat = np.percentile(exposure_collateralized, 99, axis=0)
EPE_collat = trapezoid(EE_collat, t_years) / horizon

reduction = 100.0 * (1.0 - EPE_collat / EPE_uncollat) if EPE_uncollat > 0 else 0.0
print(f"\nEPE, uncollateralized (Step 3c):  {EPE_uncollat:>14,.0f}")
print(f"EPE, collateralized (Step 4):      {EPE_collat:>14,.0f}")
print(f"Reduction:                          {reduction:>13.1f}%")

print("\nExposure profile at selected tenors (uncollateralized -> collateralized):")
for cp in [0.0, 1.0, 2.0, 3.0, 5.0, 8.0]:
    if cp > horizon + 1e-12:
        continue
    idx = np.abs(t_years - cp).argmin()
    print(f"  t={t_years[idx]:.2f}y ({time_grid.iloc[idx].date()}): "
          f"EE {EE_uncollat[idx]:>13,.0f} -> {EE_collat[idx]:>13,.0f}   "
          f"PFE_95 {PFE_95_uncollat[idx]:>13,.0f} -> {PFE_95_collat[idx]:>13,.0f}   "
          f"PFE_99 {PFE_99_uncollat[idx]:>13,.0f} -> {PFE_99_collat[idx]:>13,.0f}")

# ----------------------------------------------------------------------
# 6. Persist for Step 5 (CVA)
# ----------------------------------------------------------------------
collateralized_profile = pd.DataFrame({
    "Date": time_grid.values,
    "t_years": t_years,
    "EE": EE_collat,
    "PFE_95": PFE_95_collat,
    "PFE_99": PFE_99_collat,
})
collateralized_profile.to_csv("csa005_exposure_profile_collateralized.csv", index=False)
np.save("csa005_collateral_held.npy", collateral_held)
np.save("csa005_exposure_collateralized.npy", exposure_collateralized)

print("\nSaved: csa005_exposure_profile_collateralized.csv, "
      "csa005_collateral_held.npy, csa005_exposure_collateralized.npy")


"""PART 5: CVA (Credit Valuation Adjustment)"""



"""
Step 5: Credit Valuation Adjustment

This section calculates CVA for CSA-005 using the collateralized EE profile
 from Step 4. Fund2’s other netting sets are excluded.

The counterparty credit spread is inconsistently recorded by trade, 
so the median spread of 264.9 bps is used. The 25th and 75th percentile 
spreads are also tested as sensitivity scenarios. LGD is assumed to be 60%.

A flat hazard rate is estimated using:

*Hazard rate = Credit spread / LGD

Survival probability is then calculated as:

*Q(t) = exp(-Hazard rate × t)

CVA equals the discounted expected loss across all periods:

*CVA = LGD × Σ(Average EE × Discount Factor × Marginal PD)

Future losses are discounted at an assumed flat 4% risk-free rate.

Limitations: The model uses flat credit and discount rates because full market
curves are unavailable. Exposure and default risk are assumed to be 
independent; wrong-way risk is introduced in Step 6.
"""


VALUATION_DATE = pd.Timestamp("2025-06-15")
NETTING_SET = "CSA-005"
LGD = 0.60
RISK_FREE_RATE = 0.04

# ----------------------------------------------------------------------
# 1. Resolve Millennium's credit spread from CSA-005's live trades
# ----------------------------------------------------------------------
data = pd.read_excel("CCR_Book_Simulation_v3.xlsx")
data.columns = data.columns.astype(str).str.strip()
data = data.loc[:, ~data.columns.str.startswith("Unnamed")]

netting_set = data[data["Netting_Agreement_ID"] == NETTING_SET].copy()
netting_set["Maturity_Date"] = pd.to_datetime(netting_set["Maturity_Date"], errors="raise")
live_trades = netting_set[netting_set["Maturity_Date"] >= VALUATION_DATE].copy()

spreads = pd.to_numeric(live_trades["Counterparty_Credit_Spread_bps"], errors="raise").to_numpy(dtype=float)
if not np.isfinite(spreads).all() or (spreads <= 0).any():
    raise ValueError("All live-trade credit spreads must be positive and finite.")

CREDIT_SPREAD_BPS = float(np.median(spreads))
SPREAD_P25_BPS = float(np.percentile(spreads, 25))
SPREAD_P75_BPS = float(np.percentile(spreads, 75))

print(f"Credit-spread proxy for {NETTING_SET}: median={CREDIT_SPREAD_BPS:.1f} bps "
      f"(25th-75th percentile: {SPREAD_P25_BPS:.1f}-{SPREAD_P75_BPS:.1f} bps, "
      f"{len(spreads)} live trades)")

# ----------------------------------------------------------------------
# 2. Load Step 4's collateralized EE profile, plus Step 3c's
#    uncollateralized profile for context
# ----------------------------------------------------------------------
profile_collat = pd.read_csv("csa005_exposure_profile_collateralized.csv")
profile_uncollat = pd.read_csv("csa005_exposure_profile.csv")

required_columns = {"Date", "t_years", "EE"}
for name, prof in [("csa005_exposure_profile_collateralized.csv", profile_collat),
                    ("csa005_exposure_profile.csv", profile_uncollat)]:
    missing = required_columns - set(prof.columns)
    if missing:
        raise ValueError(f"{name} is missing: {sorted(missing)}")

time_grid = pd.to_datetime(profile_collat["Date"], errors="raise")
t_years = pd.to_numeric(profile_collat["t_years"], errors="raise").to_numpy()
EE = pd.to_numeric(profile_collat["EE"], errors="raise").to_numpy()
EE_uncollat = pd.to_numeric(profile_uncollat["EE"], errors="raise").to_numpy()

if not time_grid.iloc[0] == VALUATION_DATE or not np.isclose(t_years[0], 0.0):
    raise ValueError("The first profile date must equal VALUATION_DATE (t=0).")
if np.any(np.diff(t_years) <= 0):
    raise ValueError("The exposure time grid must be strictly increasing.")
if not np.isfinite(EE).all() or (EE < 0).any() or not np.isfinite(EE_uncollat).all() or (EE_uncollat < 0).any():
    raise ValueError("EE must be finite and non-negative.")
if len(EE) != len(t_years) or len(EE_uncollat) != len(t_years):
    raise ValueError("EE profiles do not match the time grid.")
if not (0.0 < LGD <= 1.0):
    raise ValueError("LGD must be in (0, 1].")
if RISK_FREE_RATE < 0:
    raise ValueError("The risk-free rate cannot be negative in this model.")

horizon = t_years[-1]

# ----------------------------------------------------------------------
# 3. CVA under a flat hazard rate and flat discount rate
# ----------------------------------------------------------------------
def calculate_cva(ee_profile, spread_bps):
    """Discounted, flat-hazard CVA and its period-level components."""
    hazard_rate = (spread_bps / 10_000.0) / LGD
    survival_prob = np.exp(-hazard_rate * t_years)
    marginal_pd = survival_prob[:-1] - survival_prob[1:]

    avg_ee = 0.5 * (ee_profile[:-1] + ee_profile[1:])
    midpoint_t = 0.5 * (t_years[:-1] + t_years[1:])
    discount_factor = np.exp(-RISK_FREE_RATE * midpoint_t)

    contribution = LGD * avg_ee * discount_factor * marginal_pd
    return {
        "CVA": float(contribution.sum()),
        "hazard_rate": hazard_rate,
        "survival_prob": survival_prob,
        "marginal_pd": marginal_pd,
        "avg_ee": avg_ee,
        "discount_factor": discount_factor,
        "contribution": contribution,
    }

base = calculate_cva(EE, CREDIT_SPREAD_BPS)
uncollat = calculate_cva(EE_uncollat, CREDIT_SPREAD_BPS)
low_spread = calculate_cva(EE, SPREAD_P25_BPS)
high_spread = calculate_cva(EE, SPREAD_P75_BPS)

CVA = base["CVA"]
CVA_uncollat = uncollat["CVA"]
hazard_rate = base["hazard_rate"]
survival_prob = base["survival_prob"]

print(f"\nFlat risk-free discount rate: {RISK_FREE_RATE:.2%}")
print(f"Implied flat hazard rate: {hazard_rate:.4%} per year")
print(f"Survival probability at {horizon:.2f}y: {survival_prob[-1]:.4f} "
      f"(cumulative default probability: {1 - survival_prob[-1]:.4%})")

print(f"\nCVA (collateralized, {NETTING_SET}): ${CVA:,.0f}")
print(f"CVA (uncollateralized, for context): ${CVA_uncollat:,.0f}")
reduction = 100.0 * (1 - CVA / CVA_uncollat) if CVA_uncollat > 0 else 0.0
print(f"Collateral's effect on CVA: {reduction:.1f}% reduction")

print("\nCredit-spread sensitivity, collateralized CVA:")
print(f"  Low spread  ({SPREAD_P25_BPS:.1f} bps): ${low_spread['CVA']:,.0f}")
print(f"  Base spread ({CREDIT_SPREAD_BPS:.1f} bps): ${CVA:,.0f}")
print(f"  High spread ({SPREAD_P75_BPS:.1f} bps): ${high_spread['CVA']:,.0f}")

# ----------------------------------------------------------------------
# 4. Contribution by year, for a sense of where CVA risk concentrates
# ----------------------------------------------------------------------
period_year = np.maximum(np.ceil(t_years[1:]).astype(int), 1)  # 1-indexed: "Year 1" = first year

breakdown = pd.DataFrame({
    "Start_Date": time_grid.iloc[:-1].to_numpy(),
    "End_Date": time_grid.iloc[1:].to_numpy(),
    "Year": period_year,
    "Avg_EE": base["avg_ee"],
    "Discount_Factor": base["discount_factor"],
    "Marginal_PD": base["marginal_pd"],
    "CVA_Contribution": base["contribution"],
})
yearly = breakdown.groupby("Year").agg(
    Avg_EE=("Avg_EE", "mean"),
    Marginal_PD=("Marginal_PD", "sum"),
    CVA_Contribution=("CVA_Contribution", "sum"),
)
yearly["Pct_of_CVA"] = yearly["CVA_Contribution"] / CVA * 100 if CVA > 0 else 0.0

print("\nCVA contribution by year:")
print(yearly.round(2).to_string())

# ----------------------------------------------------------------------
# 5. Persist for Step 6 (Wrong-Way Risk)
# ----------------------------------------------------------------------
breakdown.to_csv("csa005_cva_breakdown.csv", index=False)

summary = pd.DataFrame([{
    "Netting_Set": NETTING_SET,
    "Credit_Spread_bps": CREDIT_SPREAD_BPS,
    "Credit_Spread_Method": "Median of live trades",
    "Credit_Spread_P25_bps": SPREAD_P25_BPS,
    "Credit_Spread_P75_bps": SPREAD_P75_BPS,
    "LGD": LGD,
    "Risk_Free_Rate": RISK_FREE_RATE,
    "Hazard_Rate": hazard_rate,
    "Horizon_Years": horizon,
    "Survival_Prob_At_Horizon": survival_prob[-1],
    "CVA_Collateralized": CVA,
    "CVA_Uncollateralized": CVA_uncollat,
    "CVA_Low_Spread": low_spread["CVA"],
    "CVA_High_Spread": high_spread["CVA"],
}])
summary.to_csv("csa005_cva_summary.csv", index=False)

print("\nSaved: csa005_cva_breakdown.csv, csa005_cva_summary.csv")


"""PART 6: Wrong-Way Risk"""




"""
Step 6: Wrong-Way Risk

This section tests whether exposure to CSA-005 becomes high when Millennium’s
credit risk also worsens.

ITRAXX.EU is used as a proxy for Fund2’s credit spread. The proxy spread 
changes with the simulated iTraxx level:

```
Spread(t) = Starting Spread × [ITRAXX(t) / ITRAXX(0)]ᵝ
```

Beta measures how strongly Millennium’s spread responds to iTraxx. Beta 1.0 is
the base case, while 0.5 and 1.5 are sensitivity scenarios. Beta 0 reproduces
the flat-spread CVA from Step 5.

Two CVA results are compared:

* Joint CVA preserves the path-by-path relationship between exposure and 
default risk.
* Independent CVA uses the same stochastic spread distribution but separates 
it from exposure.

The difference is the within-model WWR effect:

```
WWR Add-on = Joint CVA − Independent CVA
```

A positive result indicates wrong-way risk; a negative result indicates 
right-way risk.

Limitations: iTraxx and beta are scenario assumptions rather than 
Millennium-specific estimates. Flat hazard-rate and discount-rate assumptions 
from Step 5 are retained.
"""



VALUATION_DATE = pd.Timestamp("2025-06-15")
NETTING_SET = "CSA-005"
ITRAXX_FACTOR = "ITRAXX.EU"
BASE_BETA = 1.0
BETA_SCENARIOS = (0.5, 1.0, 1.5)

# ----------------------------------------------------------------------
# 1. Load Step 5's assumptions instead of re-hardcoding them
# ----------------------------------------------------------------------
step5 = pd.read_csv("csa005_cva_summary.csv")
required_step5_columns = {"Netting_Set", "Credit_Spread_bps", "LGD", "Risk_Free_Rate",
                           "CVA_Collateralized", "CVA_Uncollateralized"}
missing = required_step5_columns - set(step5.columns)
if missing:
    raise ValueError(f"csa005_cva_summary.csv is missing: {sorted(missing)}")

step5_row = step5[step5["Netting_Set"] == NETTING_SET]
if len(step5_row) != 1:
    raise ValueError(f"csa005_cva_summary.csv must contain exactly one row for {NETTING_SET}.")
step5_row = step5_row.iloc[0]

CREDIT_SPREAD_BPS = float(step5_row["Credit_Spread_bps"])
LGD = float(step5_row["LGD"])
RISK_FREE_RATE = float(step5_row["Risk_Free_Rate"])
STEP5_CVA_COLLAT = float(step5_row["CVA_Collateralized"])
STEP5_CVA_UNCOLLAT = float(step5_row["CVA_Uncollateralized"])

if not (0.0 < LGD <= 1.0):
    raise ValueError("Step 5 LGD must be in (0, 1].")
if CREDIT_SPREAD_BPS <= 0 or RISK_FREE_RATE < 0:
    raise ValueError("Step 5 credit spread must be positive; risk-free rate must be non-negative.")

# ----------------------------------------------------------------------
# 2. Load Step 3a's simulated paths and Step 3c/4's exposure arrays
# ----------------------------------------------------------------------
paths = np.load("csa005_paths.npy")                       # (N_PATHS, n_dates, n_factors)
risk_factors = pd.read_csv("csa005_risk_factors.csv", index_col=0)
risk_factors.index = risk_factors.index.astype(str)
exposure_collat = np.load("csa005_exposure_collateralized.npy")
exposure_uncollat = np.load("csa005_exposure.npy")
profile = pd.read_csv("csa005_exposure_profile_collateralized.csv")

if ITRAXX_FACTOR not in risk_factors.index:
    raise ValueError(f"{ITRAXX_FACTOR} is not among the simulated risk factors - WWR needs it.")
if exposure_collat.shape != exposure_uncollat.shape:
    raise ValueError("Collateralized and uncollateralized exposure shapes differ.")
if paths.shape[:2] != exposure_collat.shape:
    raise ValueError("Simulated paths and exposure arrays don't align.")
if not np.isfinite(paths).all() or (paths <= 0).any():
    raise ValueError("Simulated market paths must be positive and finite.")
if not np.isfinite(exposure_collat).all() or (exposure_collat < 0).any():
    raise ValueError("Collateralized exposure must be finite and non-negative.")
if not np.isfinite(exposure_uncollat).all() or (exposure_uncollat < 0).any():
    raise ValueError("Uncollateralized exposure must be finite and non-negative.")

time_grid = pd.to_datetime(profile["Date"], errors="raise")
t_years = pd.to_numeric(profile["t_years"], errors="raise").to_numpy()
N_PATHS, n_dates = exposure_collat.shape

if time_grid.iloc[0] != VALUATION_DATE or not np.isclose(t_years[0], 0.0):
    raise ValueError("The first profile date must equal VALUATION_DATE (t=0).")
if np.any(np.diff(t_years) <= 0):
    raise ValueError("The exposure time grid must be strictly increasing.")

factors = risk_factors.index.tolist()
itraxx_idx = factors.index(ITRAXX_FACTOR)
itraxx_path = paths[:, :, itraxx_idx]                      # (N_PATHS, n_dates)
itraxx_spot = float(risk_factors.loc[ITRAXX_FACTOR, "Spot"])
if not np.allclose(itraxx_path[:, 0], itraxx_spot, atol=1e-6):
    raise ValueError("ITRAXX.EU paths do not all start at the saved starting level.")

horizon = t_years[-1]
dt = np.diff(t_years)
midpoint_t = 0.5 * (t_years[:-1] + t_years[1:])
discount_factor = np.exp(-RISK_FREE_RATE * midpoint_t)
itraxx_ratio = itraxx_path / itraxx_spot

print(f"WWR analysis for {NETTING_SET}: {N_PATHS} paths, ITRAXX.EU-linked spread, "
      f"anchored at {CREDIT_SPREAD_BPS:.1f} bps (Step 5), base beta={BASE_BETA:.1f}")

# ----------------------------------------------------------------------
# 3. Joint (path-correlated) vs Independent (same stochastic spread,
#    decoupled from exposure) CVA - the gap between them is the true,
#    isolated WWR effect
# ----------------------------------------------------------------------
def calculate_joint_cva(exposure, beta):
    """Path-specific spread/hazard/survival, then both the Joint CVA (true
    pathwise correlation preserved) and the Independent CVA (same
    stochastic spread, but averaged separately from exposure)."""
    spread_path = CREDIT_SPREAD_BPS * np.power(itraxx_ratio, beta)
    hazard_path = (spread_path / 10_000.0) / LGD

    midpoint_hazard = 0.5 * (hazard_path[:, :-1] + hazard_path[:, 1:])
    cumulative_hazard = np.cumsum(midpoint_hazard * dt[np.newaxis, :], axis=1)
    cumulative_hazard = np.column_stack([np.zeros(N_PATHS), cumulative_hazard])
    survival_path = np.exp(-cumulative_hazard)
    marginal_pd_path = survival_path[:, :-1] - survival_path[:, 1:]

    avg_exposure = 0.5 * (exposure[:, :-1] + exposure[:, 1:])
    period_contribution = LGD * discount_factor[np.newaxis, :] * avg_exposure * marginal_pd_path
    path_cva = period_contribution.sum(axis=1)

    # Independent benchmark: E[exposure] x E[marginal PD], NOT E[exposure x PD] -
    # same stochastic marginals, pathwise pairing removed.
    independent_contribution = LGD * discount_factor * avg_exposure.mean(axis=0) * marginal_pd_path.mean(axis=0)

    return {
        "CVA_Joint": float(path_cva.mean()),
        "CVA_Independent": float(independent_contribution.sum()),
        "Spread_Path": spread_path,
    }

# beta=0 must exactly reproduce Step 5 (no path variation at all) - a hard
# consistency check, not just a visual comparison.
flat_collat = calculate_joint_cva(exposure_collat, beta=0.0)
flat_uncollat = calculate_joint_cva(exposure_uncollat, beta=0.0)
if not np.isclose(flat_collat["CVA_Joint"], STEP5_CVA_COLLAT, rtol=1e-6, atol=1.0):
    raise ValueError("beta=0 does not reproduce Step 5's collateralized CVA.")
if not np.isclose(flat_uncollat["CVA_Joint"], STEP5_CVA_UNCOLLAT, rtol=1e-6, atol=1.0):
    raise ValueError("beta=0 does not reproduce Step 5's uncollateralized CVA.")

beta_results = {beta: calculate_joint_cva(exposure_collat, beta) for beta in BETA_SCENARIOS}
base = beta_results[BASE_BETA]
base_uncollat = calculate_joint_cva(exposure_uncollat, BASE_BETA)

CVA_wwr = base["CVA_Joint"]
CVA_independent = base["CVA_Independent"]
wwr_addon = CVA_wwr - CVA_independent
wwr_pct = 100.0 * wwr_addon / CVA_independent if CVA_independent > 0 else 0.0

CVA_wwr_uncollat = base_uncollat["CVA_Joint"]
CVA_independent_uncollat = base_uncollat["CVA_Independent"]
wwr_addon_uncollat = CVA_wwr_uncollat - CVA_independent_uncollat
wwr_pct_uncollat = 100.0 * wwr_addon_uncollat / CVA_independent_uncollat if CVA_independent_uncollat > 0 else 0.0

tolerance = max(1.0, 1e-6 * CVA_independent)
if wwr_addon > tolerance:
    risk_classification = "Wrong-way risk"
elif wwr_addon < -tolerance:
    risk_classification = "Right-way risk"
else:
    risk_classification = "No material directional effect"

print(f"\nCVA, Step 5 flat spread (beta=0):                         ${flat_collat['CVA_Joint']:>10,.0f}")
print(f"CVA, stochastic spread but independent exposure (beta={BASE_BETA:.1f}): ${CVA_independent:>10,.0f}")
print(f"CVA, path-linked joint result (beta={BASE_BETA:.1f}):              ${CVA_wwr:>10,.0f}")
print(f"Isolated WWR add-on: ${wwr_addon:,.0f} ({wwr_pct:+.1f}%) - {risk_classification}")

print("\nBeta sensitivity, collateralized CVA:")
for beta in BETA_SCENARIOS:
    r = beta_results[beta]
    addon_pct = 100.0 * (r["CVA_Joint"] - r["CVA_Independent"]) / r["CVA_Independent"] if r["CVA_Independent"] > 0 else 0.0
    print(f"  beta={beta:.1f}: CVA=${r['CVA_Joint']:,.0f}, isolated add-on={addon_pct:+.1f}%")

# ----------------------------------------------------------------------
# 4. Evidence: do high-spread paths also carry higher exposure?
# ----------------------------------------------------------------------
trapezoid = getattr(np, "trapezoid", None) or np.trapz
path_avg_spread = trapezoid(base["Spread_Path"], t_years, axis=1) / horizon
path_epe = trapezoid(exposure_collat, t_years, axis=1) / horizon
path_epe_uncollat = trapezoid(exposure_uncollat, t_years, axis=1) / horizon

correlation = float(np.corrcoef(path_avg_spread, path_epe)[0, 1])
correlation_uncollat = float(np.corrcoef(path_avg_spread, path_epe_uncollat)[0, 1])
print(f"\nCorrelation between a path's average spread and its own EPE: {correlation:+.3f}")

spread_rank = pd.Series(path_avg_spread).rank(method="first")  # stable tercile cuts under ties
terciles = pd.qcut(spread_rank, 3, labels=["Low spread", "Mid spread", "High spread"])
evidence = pd.DataFrame({"Tercile": terciles, "Path_Avg_Spread": path_avg_spread, "Path_EPE": path_epe})
evidence_summary = evidence.groupby("Tercile", observed=True).agg(
    Avg_Spread_bps=("Path_Avg_Spread", "mean"),
    Avg_EPE=("Path_EPE", "mean"),
    N_Paths=("Path_EPE", "count"),
)
print("\nLifetime EPE by credit-spread tercile (evidence of WWR direction):")
print(evidence_summary.round(1).to_string())

# ----------------------------------------------------------------------
# 5. Does collateral itself suppress the isolated WWR effect?
# ----------------------------------------------------------------------
print(f"\nUncollateralized isolated add-on: ${wwr_addon_uncollat:,.0f} ({wwr_pct_uncollat:+.1f}%)")
print(f"Collateralized isolated add-on:   ${wwr_addon:,.0f} ({wwr_pct:+.1f}%)")
collateral_effect = ("Collateral reduced the absolute directional add-on." if abs(wwr_addon) < abs(wwr_addon_uncollat)
                      else "Collateral increased the absolute directional add-on." if abs(wwr_addon) > abs(wwr_addon_uncollat)
                      else "Collateral did not change the absolute directional add-on.")
print(collateral_effect)

# ----------------------------------------------------------------------
# 6. Persist results
# ----------------------------------------------------------------------
summary = pd.DataFrame([{
    "Netting_Set": NETTING_SET,
    "Risk_Type": risk_classification,
    "Credit_Spread_Anchor_bps": CREDIT_SPREAD_BPS,
    "ITRAXX_Beta_Base": BASE_BETA,
    "LGD": LGD,
    "Risk_Free_Rate": RISK_FREE_RATE,
    "CVA_Step5_Flat": flat_collat["CVA_Joint"],
    "CVA_Independent": CVA_independent,
    "CVA_WWR": CVA_wwr,
    "WWR_Addon": wwr_addon,
    "WWR_Addon_Pct": wwr_pct,
    "Spread_EPE_Correlation": correlation,
    "CVA_Beta_0_5": beta_results[0.5]["CVA_Joint"],
    "CVA_Beta_1_0": beta_results[1.0]["CVA_Joint"],
    "CVA_Beta_1_5": beta_results[1.5]["CVA_Joint"],
    "CVA_Independent_Uncollateralized": CVA_independent_uncollat,
    "CVA_WWR_Uncollateralized": CVA_wwr_uncollat,
    "WWR_Addon_Uncollateralized": wwr_addon_uncollat,
    "WWR_Addon_Pct_Uncollateralized": wwr_pct_uncollat,
    "Spread_EPE_Correlation_Uncollateralized": correlation_uncollat,
    "Collateral_Effect_On_Absolute_Addon": collateral_effect,
}])
summary.to_csv("csa005_wwr_summary.csv", index=False)
evidence_summary.to_csv("csa005_wwr_tercile_evidence.csv")

print("\nSaved: csa005_wwr_summary.csv, csa005_wwr_tercile_evidence.csv")

















































