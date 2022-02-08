#%%
import argparse
from email.policy import default
import warnings

# Standard imports
import numpy as np
import pandas as pd
import torch

# For Gower distance
import gower

# For data preprocessing
from rdt import HyperTransformer
from sklearn.preprocessing import OneHotEncoder, StandardScaler
from sklearn_pandas import DataFrameMapper

from opacus.utils.uniform_sampler import UniformWithReplacementSampler

# For the SUPPORT dataset
from pycox.datasets import support

# For VAE dataset formatting
from torch.utils.data import TensorDataset, DataLoader

# VAE functions
from VAE import Decoder, Encoder, VAE

# SDV aspects
from sdv.evaluation import evaluate

from sdv.metrics.tabular import NumericalLR, NumericalMLP, NumericalSVR

#torch.cuda.is_available()
#torch.cuda.current_device()
#torch.cuda.get_device_name(0)

# Load in the support data
data_supp = support.read_df()

###############################################################################
# DATA PREPROCESSING #
# We one-hot the categorical cols and standardise the continuous cols
data_supp["x14"] = data_supp["x0"]
# data_supp = data_supp.astype('float32')
data_supp = data_supp[
    ["duration"] + [f"x{i}" for i in range(1, 15)] + ["event"]
]
data_supp[["x1", "x2", "x3", "x4", "x5", "x6", "event"]] = data_supp[
    ["x1", "x2", "x3", "x4", "x5", "x6", "event"]
].astype(int)

# As of coding this, new version of RDT adds in GMM transformer which is what we require, however hyper transformers do not work as individual
# transformers take a 'columns' argument that can only allow for fitting of one column - so you need to loop over and create one for each column
# in order to fit the dataset - https://github.com/sdv-dev/RDT/issues/376

from rdt.transformers import categorical, numerical, boolean, datetime

continuous_transformers = {}
categorical_transformers = {}
boolean_transformers = {}
datetime_transformers = {}

continuous_columns = ['duration'] + [f"x{i}" for i in range(7,15)]
categorical_columns = ['event'] + [f"x{i}" for i in range(1,7)] 
num_categories = (
    np.array([np.amax(data_supp[col]) for col in categorical_columns]) + 1
).astype(int)

transformed_dataset = data_supp

# Define columns based on datatype and then loop over creating and fitting transformers

# Do continuous first via GMM as it gives a mixture column that then needs to be encoded OHE
for index, column in enumerate(continuous_columns):

    temp_continuous = numerical.BayesGMMTransformer()
    temp_continuous.fit(transformed_dataset, columns = column)
    continuous_transformers['continuous_{}'.format(index)] = temp_continuous

    transformed_dataset = temp_continuous.transform(transformed_dataset)

    # Each numerical one gets a .normalized column + a .component column giving the mixture info
    # This too needs to be one hot encoded

    categorical_columns += [str(column) + '.component']
    normalised_column = str(column) + '.component'

# Let's retrieve the new categorical and continuous column names

continuous_columns = ['duration.normalized'] + [f"x{i}.normalized" for i in range(7,15)]

# For each categorical column we want to know the number of categories

num_categories = (
    np.array([np.amax(transformed_dataset[col]) for col in categorical_columns]) + 1
).astype(int)

num_continuous = len(continuous_columns)

for index, column in enumerate(categorical_columns):

    temp_categorical = categorical.OneHotEncodingTransformer()
    temp_categorical.fit(transformed_dataset, columns = column)
    categorical_transformers['categorical_{}'.format(index)] = temp_categorical

    transformed_dataset = temp_categorical.transform(transformed_dataset)

# We need the dataframe in the correct format i.e. categorical variables first and in the order of
# num_categories with continuous variables placed after
#%%
reordered_dataframe = pd.DataFrame()

reordered_dataframe = transformed_dataset.iloc[:, num_continuous:]

reordered_dataframe = pd.concat([reordered_dataframe, transformed_dataset.iloc[:, :num_continuous]], axis=1)

#%%

x_train_df = reordered_dataframe.to_numpy()
x_train = x_train_df.astype("float32")
###############################################################################
# Prepare data for interaction with torch VAE
Y = torch.Tensor(x_train)
dataset = TensorDataset(Y)
batch_size = 32

generator = None
sample_rate = batch_size / len(dataset)
data_loader = DataLoader(
    dataset,
    batch_sampler=UniformWithReplacementSampler(
        num_samples=len(dataset), sample_rate=sample_rate, generator=generator
    ),
    pin_memory=True,
    generator=generator,
)

# Create VAE
latent_dim = 256
hidden_dim = 256
encoder = Encoder(x_train.shape[1], latent_dim, hidden_dim=hidden_dim)
decoder = Decoder(
    latent_dim, num_continuous, num_categories=num_categories
)
vae = VAE(encoder, decoder)

n_epochs = 200

log_elbo, log_reconstruction, log_divergence, log_categorical, log_numerical = vae.train(data_loader, n_epochs=n_epochs)

#%% -------- Plotting features for loss -------- #

import matplotlib.pyplot as plt

# Plot and save the breakdown of ELBO

x1 = np.arange(n_epochs)
y1 = log_elbo
# plotting the elbo
plt.plot(x1, y1, label = "ELBO")
# line 2 points
y2 = log_reconstruction
# plotting the reconstruction term
plt.plot(x1, y2, label = "Reconstruction Term")
# plotting the divergence term
plt.plot(x1, log_divergence, label = "Divergence Term")
plt.xlabel('Number of Epochs')
# Set the y axis label of the current axis.
plt.ylabel('Loss Value')
# Set a title of the current axes.
plt.title('Breakdown of the ELBO - 256 Latent Dim')
# show a legend on the plot
plt.legend()
plt.savefig("Elbo Breakdown at 256 Latent Dim.png")
# Display a figure.
plt.show()

# Plot and save the breakdown of the reconstruction term

# Clear plot after saving
plt.clf()

x1 = np.arange(n_epochs)
y1 = log_categorical
# plotting the elbo
plt.subplot(1,2,1)
plt.plot(x1, y1, label = "Categorical Reconstruction")
# line 2 points
y2 = log_numerical
# plotting the reconstruction term
plt.subplot(1,2,2)
plt.plot(x1, y2, label = "Numerical Reconstruction")
plt.xlabel('Number of Epochs')
# Set the y axis label of the current axis.
plt.ylabel('Loss Value')
# Set a title of the current axes.
plt.title('Breakdown of the Reconstruction Term - 256 Latent Dim')
# show a legend on the plot
plt.legend()
plt.tight_layout()
plt.savefig("Reconstruction Breakdown at 256 Latent Dim.png")
# Display a figure.
plt.show()
#%% -------- Plotting features for synthetic data distribution -------- #

# Generate a synthetic set using trained vae

synthetic_trial = vae.generate(8873) # 8873 is size of support

#%%

print(synthetic_trial[:,1].detach().numpy())
#%%
# Now choose columns you want to do histograms for (for sake of brevity) and compare to support visually

cat_columns = [1, 4]
cont_columns = [110, 114]

for column in cat_columns:
    # Plot these cat_columns against the original columns in x_train
    plt.subplot(1,2,1)
    plt.hist(synthetic_trial[:,column].detach().numpy())
    plt.xlabel("Value")
    plt.ylabel("Counts")
    plt.title("Synthetic Arm - Categorical {}".format(str(column)))
    plt.subplot(1,2,2)
    plt.hist(x_train[:,column])
    plt.xlabel("Value")
    plt.ylabel("Counts")
    plt.title("Original Arm - Categorical {}".format(str(column)))
    plt.tight_layout()
    plt.savefig("Categorical Histogram Comparison.png")
    plt.show()

for column in cont_columns:
    # Plot these cont_columns against the original columns in x_train
    plt.subplot(1,2,1)
    plt.hist(synthetic_trial[:,column].detach().numpy())
    plt.xlabel("Value")
    plt.ylabel("Counts")
    plt.title("Synthetic Arm - Continuous {}".format(str(column)))
    plt.subplot(1,2,2)
    plt.hist(x_train[:,column])
    plt.xlabel("Value")
    plt.ylabel("Counts")
    plt.title("Original Arm - Continuous {}".format(str(column)))
    plt.tight_layout()
    plt.savefig("Continuous Histogram Comparison.png")
    plt.show()

#%%

# - Now we want to view how these continuous variables look once reverse
# transformed using the gmm transformer

# First add the old columns to the synthetic set to see what corresponds to what

synthetic_dataframe = pd.DataFrame(synthetic_trial.detach().numpy(),  columns=reordered_dataframe.columns)

# Now all of the transformations from the dictionary - first loop over the categorical columns

synthetic_transformed_set = synthetic_dataframe

for transformer_name in categorical_transformers:

    transformer = categorical_transformers[transformer_name]
    synthetic_transformed_set = transformer.reverse_transform(synthetic_transformed_set)

for transformer_name in continuous_transformers:

    transformer = continuous_transformers[transformer_name]
    synthetic_transformed_set = transformer.reverse_transform(synthetic_transformed_set)

#%%

# Plot some examples

cat_columns = ['x1', 'x4']
cont_columns = ['duration', 'x10']

for column in cat_columns:
    # Plot these cat_columns against the original columns in x_train
    plt.subplot(1,2,1)
    plt.hist(synthetic_transformed_set[column])
    plt.xlabel("Value")
    plt.ylabel("Counts")
    plt.title("Synthetic Arm - {}".format(column))
    plt.subplot(1,2,2)
    plt.hist(data_supp[column])
    plt.xlabel("Value")
    plt.ylabel("Counts")
    plt.title("Original Arm - {}".format(column))
    plt.tight_layout()
    plt.savefig("Categorical Histogram Comparison Transformed.png")
    plt.show()

for column in cont_columns:
    # Plot these cont_columns against the original columns in x_train
    plt.subplot(1,2,1)
    plt.hist(synthetic_transformed_set[column])
    plt.xlabel("Value")
    plt.ylabel("Counts")
    plt.title("Synthetic Arm - {}".format(column))
    plt.subplot(1,2,2)
    plt.hist(data_supp[column])
    plt.xlabel("Value")
    plt.ylabel("Counts")
    plt.title("Original Arm - {}".format(column))
    plt.tight_layout()
    plt.savefig("Continuous Histogram Comparison Transformed.png")
    plt.show()