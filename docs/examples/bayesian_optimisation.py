# %% [markdown]
# # Introduction to Bayesian Optimisation
#
# In this guide we introduce the Bayesian Optimisation (BO) paradigm for
# optimising black-box functions. We'll assume an understanding of Gaussian processes
# (GPs), so if you're not familiar with them, check out our [GP introduction notebook](https://docs.jaxgaussianprocesses.com/examples/intro_to_gps/).

# %%
# Enable Float64 for more stable matrix inversions.
from jax.config import config

config.update("jax_enable_x64", True)

import jax
from jax import jit
import jax.numpy as jnp
import jax.random as jr
from jaxtyping import install_import_hook, Float, Int
import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib import cm
import optax as ox
import tensorflow_probability.substrates.jax as tfp
from typing import List, Tuple

with install_import_hook("gpjax", "beartype.beartype"):
    import gpjax as gpx
from gpjax.typing import Array, FunctionalSample, ScalarFloat
from jaxopt import ScipyBoundedMinimize

key = jr.PRNGKey(42)
plt.style.use(
    "https://raw.githubusercontent.com/JaxGaussianProcesses/GPJax/main/docs/examples/gpjax.mplstyle"
)
cols = mpl.rcParams["axes.prop_cycle"].by_key()["color"]


# %% [markdown]
# ## Some Motivating Examples
#
# Countless problems in the physical world involve optimising functions for which the
# explicit functional form is unknown, but which can be expensively queried throughout
# their domain. For example, within the domain of science the task of designing new
# molecules with optimised properties ([Griffiths and Lobato,
# 2020](https://pubs.rsc.org/en/content/articlehtml/2019/sc/c9sc04026a)) is incredibly
# useful. Here, the domain being optimised over is the space of possible molecules, with
# the objective function depending on the property being optimised, for instance within
# drug-design this may be the efficacy of the drug. The function from molecules to
# efficacy is unknown, but can be queried by synthesising a molecule and running an
# experiment to measure its efficacy. This is clearly an expensive procedure!
#
# Within the domain of machine learning, the task of optimising neural network
# architectures is another example of such a problem (commonly referred to as [Neural
# Architecture Search (NAS)](https://en.wikipedia.org/wiki/Neural_architecture_search)).
# Here, the domain is the space of possible neural network architectures, and the
# objective function is a metric such as the accuracy of the trained model. Again, the
# function from neural network architectures to accuracy is unknown, but can be queried by
# training a model with a given architecture and evaluating its accuracy. This is also an
# expensive procedure, as training models can be incredibly time consuming and
# computationally demanding.
#
# Finally, these problems are ubiquitous within the field of climate science, with
# ([Hellan et al., 2023](https://arxiv.org/abs/2306.04343)) providing several excellent
# examples. One such example is the task of deciding where to place wind turbines in a
# wind farm in order to maximise the energy generated. Here, the domain is the space of
# possible locations for the wind turbines, and the objective function is the energy
# generated by the wind farm. The function from locations to energy generated is unknown,
# but could be queried by running a simulation of the wind farm with the turbines placed
# at a given set of locations. Running such simulations can be expensive, particularly if
# they are high-fidelity.
#
# At the heart of all these problems is the task of optimising a function for which we
# don't have the explicit functional form, but which we can (expensively) query at any
# point in its domain. Bayesian optimisation provides a principled framework for solving
# such problems.

# %% [markdown]
# ## What is Bayesian Optimisation?
#
# Bayesian optimisation (BO) ([Močkus, 1974](https://link.springer.com/chapter/10.1007/3-540-07165-2_55)) provides a principled
# method for making decisions under uncertainty. The aim of BO is to find the global
# minimum of a *black-box* objective function, $\min_{\mathbf{x} \in X}
# f(\mathbf{x})$. The function $f$ is said to be a *black-box* function because its
# explicit functional form is unknown. However, it is assumed that one is able to
# ascertain information about the function by evaluating it at points in its domain,
# $X$. However, these evaluations are assumed to be *expensive*, as seen in the
# motivating examples. Therefore, the goal of BO is to minimise $f$ with as few
# evaluations of the black-box function as possible.
#
# As such, BO can be thought of as *sequential decision-making* problem. At each iteration
# one must choose which point (or batch of points) in a function's domain to evaluate
# next, drawing on previously observed values to make optimal decisions. In order to do
# this effectively, we need a way of representing our uncertainty about the black-box
# function $f$, which we can update in light of observing more data. Gaussian processes
# will be an ideal tool for this purpose!
#
# *Surrogate models* lie at the heart of BO, and are used to model the black-box
# function. GPs are a natural choice for this model, as they not only provide point
# estimates for the values taken by the function throughout its domain, but crucially
# provide a full predictive posterior *distribution* of the range of values the function
# may take. This rich quantification of uncertainty enables BO to balance *exploration*
# and *exploitation* in order to efficiently converge upon minima.
#
# Having chosen a surrogate model, which we can use to express our current beliefs about
# the black-box function, ideally we would like a method which can use the surrogate
# model's posterior distribution to automatically decide which point(s) in the black-box
# function's domain to query next. This is where *acquisition functions* come in. The
# acquisition function $\alpha: X \to \mathbb{R}$ is defined over the same domain as the
# surrogate model, and uses the surrogate model's posterior distribution to quantify the
# expected *utility*, $U$, of evaluating the black-box function at a given point. Simply
# put, for each point in the black-box function's domain, $\mathbf{x} \in X$, the
# acquisition function quantifies how useful it would be to evaluate the black-box
# function at $\mathbf{x}$ in order to find the minimum of the black-box function, whilst
# taking into consideration all the datapoints observed so far. Therefore, in order to
# decide which point to query next we simply choose the point which maximises the
# acquisition function, using an optimiser such as L-BFGS ([Liu and Nocedal,
# 1989](https://link.springer.com/article/10.1007/BF01589116)).
#
# The Bayesian optimisation loop can be summarised as follows, with $i$ denoting the
# current iteration:
#
# 1. Select the next point to query, $\mathbf{x}_{i}$, by maximising the acquisition function $\alpha$, defined using the surrogate model $\mathcal{M}_i$ conditioned on previously observed data $\mathcal{D}_i$:
#
# $$\mathbf{x}_{i} = \arg\max_{\mathbf{x}} \alpha (\mathbf{x}; \mathcal{D}_i,
# \mathcal{M}_i)$$
#
# 2. Evaluate the objective function at $\mathbf{x}_i$, yielding observation $y_i =
#    f(\mathbf{x}_i)$.
#
# 3. Append the most recent observation to the dataset, $\mathcal{D}_{i+1} = \mathcal{D}_i
#    \cup \{(\mathbf{x}_i, y_i)\}$.
#
# 4. Condition the model on the updated dataset to yield $\mathcal{M}_{i+1}$.
#
# This process is repeated until some stopping criterion is met, such as a function
# evaluation budget being exhausted.
#
# There are a plethora of acquisition functions to choose from, each with their own
# advantages and disadvantages, of which ([Shahriari et al., 2015](https://www.cs.ox.ac.uk/people/nando.defreitas/publications/BayesOptLoop.pdf))
# provides an excellent overview.
#
# In this guide we will focus on *Thompson sampling*, a conceptually simple yet effective
# method for characterising the utility of querying points in a black-box function's
# domain, which will be useful in demonstrating the key aspects of BO.

# %% [markdown]
# ## Thompson Sampling
#
# Thompson sampling ([Thompson, 1933](https://www.dropbox.com/s/yhn9prnr5bz0156/1933-thompson.pdf)) is a simple method which
# naturally balances exploration and exploitation. The core idea is to, at each iteration
# of the BO loop, sample a function, $g$, from the posterior distribution of the surrogate
# model $\mathcal{M}_i$, and then evaluate the black-box function at the point(s) which
# minimise this sample. Given a sample $g$, from the posterior distribution given by the model $\mathcal{M}_i$ the Thompson sampling utility function is defined as:
#
# $$U_{\text{TS}}(\mathbf{x}; \mathcal{D}_i, \mathcal{M}_i) = - g(\mathbf{x})$$
#
# Note the negative sign; this is included as we want to maximise the *utility* of
# evaluating the black-box function $f$ at a given point. We interested in finding the
# minimum of $f$, so we maximise the negative of the sample from the posterior distribution $g$.
#
# As a toy example, we shall be applying BO to the widely used [Forrester
# function](https://www.sfu.ca/~ssurjano/forretal08.html):
#
# $$f(x) = (6x - 2)^2 \sin(12x - 4)$$
#
# treating $f$ as a black-box function. Moreover, we shall restrict the domain of the
# function to $\mathbf{x} \in [0, 1]$. The global minimum of this function is located at
# $x = 0.757$, where $f(x) = -6.021$.


# %%
def forrester(x: Float[Array, "N 1"]) -> Float[Array, "N 1"]:
    return (6 * x - 2) ** 2 * jnp.sin(12 * x - 4)


# %% [markdown]
# We'll first go through one iteration of the BO loop step-by-step, before wrapping this
# up in a loop to perform the full optimisation.

# %% [markdown]
# First we'll specify the domain over which we wish to optimise the function, as well as
# sampling some initial points for fitting our surrogate model using a space-filling design.

# %%
lower_bound = jnp.array([0.0])
upper_bound = jnp.array([1.0])
initial_sample_num = 5

initial_x = tfp.mcmc.sample_halton_sequence(
    dim=1, num_results=initial_sample_num, seed=key, dtype=jnp.float64
).reshape(-1, 1)
initial_y = forrester(initial_x)
D = gpx.Dataset(X=initial_x, y=initial_y)


# %% [markdown]
# Next we'll define our GP model in the usual way, using a Matérn52 kernel, and fit the
# kernel parameters by minimising the negative log-marginal likelihood. We'll wrap this in
# a function as we'll be repeating this process at each iteration of the BO loop.


# %%
def return_optimised_posterior(
    data: gpx.Dataset, prior: gpx.Module, key: Array
) -> gpx.Module:
    likelihood = gpx.Gaussian(
        num_datapoints=data.n, obs_noise=jnp.array(1e-6)
    )  # Our function is noise-free, so we set the observation noise to a very small value
    likelihood = likelihood.replace_trainable(obs_noise=False)

    posterior = prior * likelihood

    negative_mll = gpx.objectives.ConjugateMLL(negative=True)
    negative_mll(posterior, train_data=data)
    negative_mll = jit(negative_mll)

    opt_posterior, history = gpx.fit(
        model=posterior,
        objective=negative_mll,
        train_data=data,
        optim=ox.adam(learning_rate=0.01),
        num_iters=1000,
        safe=True,
        key=key,
        verbose=False,
    )

    return opt_posterior


mean = gpx.mean_functions.Zero()
kernel = gpx.kernels.Matern52()
prior = gpx.Prior(mean_function=mean, kernel=kernel)
opt_posterior = return_optimised_posterior(D, prior, key)

# %% [markdown]
# We can then sample a function from the posterior distribution of the surrogate model. We
# will do this using the `sample_approx` method, which generates an approximate sample
# from the posterior using decoupled sampling introduced in ([Wilson et al.,
# 2020](https://proceedings.mlr.press/v119/wilson20a.html)) and discussed in our [Pathwise
# Sampling Notebook](https://docs.jaxgaussianprocesses.com/examples/spatial/). This method
# is used as it enables us to sample from the posterior in a manner which scales linearly
# with the number of points sampled, $O(N)$, mitigating the cubic cost associated with
# drawing exact samples from a GP posterior, $O(N^3)$. It also generates more accurate
# samples than many other methods for drawing approximate samples from a GP posterior.
#
# Note that we also define a `utility_fn` which calls the approximate
# sample but returns the value returned as a scalar. This is because the `sample_approx`
# function returns an array of shape $[N, B]$, with $N$ being the number of points within
# each sample and $B$ being the number of samples drawn. We'll only be drawing (and
# optimising) one sample at a time, and our optimiser requires the function being
# optimised to return a scalar output (only querying it at $N=1$ points), so we'll remove the axes from the returned value.

# %%
approx_sample = opt_posterior.sample_approx(
    num_samples=1, train_data=D, key=key, num_features=500
)
utility_fn = lambda x: approx_sample(x)[0][0]


# %% [markdown]
# In order to minimise the sample, we'll be using the L-BFGS-B ([Byrd et al., 1995](https://epubs.siam.org/doi/abs/10.1137/0916069)) optimiser from the `jaxopt`
# library. This is a gradient-based optimiser which performs optimisation within a bounded
# domain. In order to perform optimisation, this optimiser requires a point to start from.
# Therefore, we will first query our sample from the posterior at a random set of points,
# and then use the lowest point from this set of points as the starting point for the
# optimiser. In this example we'll sample 100 points from the posterior, due to the simple
# nature of the Forrester function. However, in practice it can be beneficial to
# adopt a more sophisticated approach, and there are several heuristics available in the
# literature (see for example ([Le Riche and Picheny,
# 2021](https://arxiv.org/abs/2103.16649))). For instance, one may randomly sample the
# posterior at a number of points proportional to the dimensionality of the input space,
# and one may run gradient-based optimisation from multiple of these points, to reduce the
# risk of converging upon local minima.


# %%
def optimise_sample(
    sample: FunctionalSample,
    key: Int[Array, ""],
    lower_bound: Float[Array, "D"],
    upper_bound: Float[Array, "D"],
    num_initial_sample_points: int,
) -> ScalarFloat:
    initial_sample_points = jr.uniform(
        key,
        shape=(num_initial_sample_points, lower_bound.shape[0]),
        dtype=jnp.float64,
        minval=lower_bound,
        maxval=upper_bound,
    )
    initial_sample_y = sample(initial_sample_points)
    best_x = jnp.array([initial_sample_points[jnp.argmin(initial_sample_y)]])

    # We want to maximise the utility function, but the optimiser performs minimisation. Since we're minimising the sample drawn, the sample is actually the negative utility function.
    negative_utility_fn = lambda x: sample(x)[0][0]
    lbfgsb = ScipyBoundedMinimize(fun=negative_utility_fn, method="l-bfgs-b")
    bounds = (lower_bound, upper_bound)
    x_star = lbfgsb.run(best_x, bounds=bounds).params
    return x_star


x_star = optimise_sample(approx_sample, key, lower_bound, upper_bound, 100)
y_star = forrester(x_star)


# %% [markdown]
# Having found the minimum of the sample from the posterior, we can then evaluate the
# black-box objective function at this point, and append the new observation to our dataset.
#
# Below we plot the posterior distribution of the surrogate model, along with the sample
# drawn from the model, and the minimiser of this sample returned from the optimiser,
# which we denote with a star.


# %%
def plot_bayes_opt(
    posterior: gpx.Module,
    sample: FunctionalSample,
    dataset: gpx.Dataset,
    queried_x: ScalarFloat,
) -> None:
    plt_x = jnp.linspace(0, 1, 1000).reshape(-1, 1)
    forrester_y = forrester(plt_x)
    sample_y = sample(plt_x)

    latent_dist = posterior.predict(plt_x, train_data=dataset)
    predictive_dist = posterior.likelihood(latent_dist)

    predictive_mean = predictive_dist.mean()
    predictive_std = predictive_dist.stddev()

    fig, ax = plt.subplots()
    ax.fill_between(
        plt_x.squeeze(),
        predictive_mean - 2 * predictive_std,
        predictive_mean + 2 * predictive_std,
        alpha=0.2,
        label="Two sigma",
        color=cols[1],
    )
    ax.plot(
        plt_x,
        predictive_mean - 2 * predictive_std,
        linestyle="--",
        linewidth=1,
        color=cols[1],
    )
    ax.plot(
        plt_x,
        predictive_mean + 2 * predictive_std,
        linestyle="--",
        linewidth=1,
        color=cols[1],
    )
    ax.plot(
        plt_x,
        forrester_y,
        label="Forrester Function",
        color=cols[0],
        linestyle="--",
        linewidth=2,
    )
    ax.plot(plt_x, predictive_mean, label="Predictive Mean", color=cols[1])
    ax.plot(plt_x, sample_y, label="Posterior Sample")
    ax.scatter(dataset.X, dataset.y, label="Observations", color=cols[2], zorder=2)
    ax.scatter(
        queried_x,
        sample(queried_x),
        label="Posterior Sample Optimum",
        marker="*",
        color=cols[3],
        zorder=3,
    )
    ax.legend(loc="center left", bbox_to_anchor=(0.975, 0.5))
    plt.show()


plot_bayes_opt(opt_posterior, approx_sample, D, x_star)

# %% [markdown]
# At this point we can update our model with the newly augmented dataset, and repeat the
# whole process until some stopping criterion is met. Below we repeat this process for 10
# iterations, printing out the queried point and the value of the black-box function at
# each iteration.

# %%
bo_iters = 5

# Set up initial dataset
initial_x = tfp.mcmc.sample_halton_sequence(
    dim=1, num_results=initial_sample_num, seed=key, dtype=jnp.float64
).reshape(-1, 1)
initial_y = forrester(initial_x)
D = gpx.Dataset(X=initial_x, y=initial_y)

for i in range(bo_iters):
    key, subkey = jr.split(key)

    # Generate optimised posterior using previously observed data
    mean = gpx.mean_functions.Zero()
    kernel = gpx.kernels.Matern52()
    prior = gpx.Prior(mean_function=mean, kernel=kernel)
    opt_posterior = return_optimised_posterior(D, prior, subkey)

    # Draw a sample from the posterior, and find the minimiser of it
    approx_sample = opt_posterior.sample_approx(
        num_samples=1, train_data=D, key=subkey, num_features=500
    )
    x_star = optimise_sample(
        approx_sample, subkey, lower_bound, upper_bound, num_initial_sample_points=100
    )

    plot_bayes_opt(opt_posterior, approx_sample, D, x_star)

    # Evaluate the black-box function at the best point observed so far, and add it to the dataset
    y_star = forrester(x_star)
    print(f"Queried Point: {x_star}, Black-Box Function Value: {y_star}")
    D = D + gpx.Dataset(X=x_star, y=y_star)

# %% [markdown]
# Below we plot the best observed black-box function value against the number of times
# the black-box function has been evaluated. Note that the first 5 samples are randomly
# sampled to fit the initial GP model, and we denote the start of using BO to sample with
# the dotted vertical line.
#
# We can see that the BO algorithm quickly converges to the global minimum of the
# black-box function!
#

# %%
fig, ax = plt.subplots()
fn_evaluations = jnp.arange(1, bo_iters + initial_sample_num + 1)
cumulative_best_y = jax.lax.associative_scan(jax.numpy.minimum, D.y)
ax.plot(fn_evaluations, cumulative_best_y)
ax.axvline(x=initial_sample_num, linestyle=":")
ax.axhline(y=-6.0207, linestyle="--", label="True Minimum")
ax.set_xlabel("Number of Black-Box Function Evaluations")
ax.set_ylabel("Best Observed Value")
ax.legend()
plt.show()


# %% [markdown]
# ### A More Challenging Example - The Six-Hump Camel Function

# %% [markdown]
# We'll now apply BO to a more challenging example, the [Six-Hump Camel
# Function](https://www.sfu.ca/~ssurjano/camel6.html). This is a function of two inputs
# defined as follows:
#
# $$f(x_1, x_2) = (4 - 2.1x_1^2 + \frac{x_1^4}{3})x_1^2 + x_1x_2 + (-4 + 4x_2^2)x_2^2$$
#
# We'll be evaluating it over the domain $x_1 \in [-2, 2]$ and $x_2 \in [-1, 1]$. The
# global minima of this function are located at $\mathbf{x} = (0.0898, -0.7126)$ and $\mathbf{x} = (-0.0898, 0.7126)$, where the function takes the value $f(\mathbf{x}) = -1.0316$.


# %%
def six_hump_camel(x: Float[Array, "N 2"]) -> Float[Array, "N 1"]:
    x1 = x[..., :1]
    x2 = x[..., 1:]
    term1 = (4 - 2.1 * x1**2 + x1**4 / 3) * x1**2
    term2 = x1 * x2
    term3 = (-4 + 4 * x2**2) * x2**2
    return term1 + term2 + term3


# %% [markdown]
# First, we'll visualise the function over the domain of interest:

# %%
x1 = jnp.linspace(-2, 2, 100)
x2 = jnp.linspace(-1, 1, 100)
x1, x2 = jnp.meshgrid(x1, x2)
x = jnp.stack([x1.flatten(), x2.flatten()], axis=1)
y = six_hump_camel(x)

fig, ax = plt.subplots(subplot_kw={"projection": "3d"})
surf = ax.plot_surface(
    x1,
    x2,
    y.reshape(x1.shape[0], x2.shape[0]),
    linewidth=0,
    cmap=cm.coolwarm,
    antialiased=False,
)
ax.set_xlabel("x1")
ax.set_ylabel("x2")
plt.show()

# %% [markdown]
# For more clarity, we can generate a contour plot of the function which enables us to see
# the global minima of the function more clearly.

# %%
x_star_one = jnp.array([[0.0898, -0.7126]])
x_star_two = jnp.array([[-0.0898, 0.7126]])
fig, ax = plt.subplots()
contour_plot = ax.contourf(
    x1, x2, y.reshape(x1.shape[0], x2.shape[0]), cmap=cm.coolwarm, levels=40
)
ax.scatter(
    x_star_one[0][0], x_star_one[0][1], marker="*", color=cols[2], label="Global Minima"
)
ax.scatter(x_star_two[0][0], x_star_two[0][1], marker="*", color=cols[2])
ax.set_xlabel("x1")
ax.set_ylabel("x2")
fig.colorbar(contour_plot)
ax.legend()
plt.show()

# %% [markdown]
# Next, we'll run the BO loop using Thompson sampling as before. This time we'll run the
# experiment 5 times in order to see how the algorithm performs on average, with different
# starting points for the initial GP model. This is good practice, as the performance
# obtained is likely to vary between runs depending on the initialisation samples used to
# fit the initial GP model.

# %%
lower_bound = jnp.array([-2.0, -1.0])
upper_bound = jnp.array([2.0, 1.0])
initial_sample_num = 5
bo_iters = 11
num_experiments = 5
bo_experiment_results = []

for experiment in range(num_experiments):
    print(f"Starting Experiment: {experiment + 1}")
    # Set up initial dataset
    initial_x = tfp.mcmc.sample_halton_sequence(
        dim=2, num_results=initial_sample_num, seed=key, dtype=jnp.float64
    )
    initial_x = jnp.array(lower_bound + (upper_bound - lower_bound) * initial_x)
    initial_y = six_hump_camel(initial_x)
    D = gpx.Dataset(X=initial_x, y=initial_y)

    for i in range(bo_iters):
        key, subkey = jr.split(key)

        # Generate optimised posterior
        mean = gpx.mean_functions.Zero()
        kernel = gpx.kernels.Matern52(
            active_dims=[0, 1], lengthscale=jnp.array([1.0, 1.0]), variance=2.0
        )
        prior = gpx.Prior(mean_function=mean, kernel=kernel)
        opt_posterior = return_optimised_posterior(D, prior, subkey)

        # Draw a sample from the posterior, and find the minimiser of it
        approx_sample = opt_posterior.sample_approx(
            num_samples=1, train_data=D, key=subkey, num_features=500
        )
        x_star = optimise_sample(
            approx_sample,
            subkey,
            lower_bound,
            upper_bound,
            num_initial_sample_points=1000,
        )

        # Evaluate the black-box function at the best point observed so far, and add it to the dataset
        y_star = six_hump_camel(x_star)
        print(
            f"BO Iteration: {i + 1}, Queried Point: {x_star}, Black-Box Function Value: {y_star}"
        )
        D = D + gpx.Dataset(X=x_star, y=y_star)
    bo_experiment_results.append(D)


# %% [markdown]
# We'll also run a random benchmark, whereby we randomly sample from the search space for
# 20 iterations. This is a useful benchmark to compare the performance of BO against in
# order to ascertain how much of an advantage BO provides over such a simple approach.
#

# %%
random_experiment_results = []
for i in range(num_experiments):
    key, subkey = jr.split(key)
    initial_x = bo_experiment_results[i].X[:5]
    initial_y = bo_experiment_results[i].y[:5]
    final_x = jr.uniform(
        key,
        shape=(bo_iters, 2),
        dtype=jnp.float64,
        minval=lower_bound,
        maxval=upper_bound,
    )
    final_y = six_hump_camel(final_x)
    random_x = jnp.concatenate([initial_x, final_x], axis=0)
    random_y = jnp.concatenate([initial_y, final_y], axis=0)
    random_experiment_results.append(gpx.Dataset(X=random_x, y=random_y))


# %% [markdown]
# Finally, we'll process the experiment results to find the log regret at each iteration
# of the experiments. The regret is defined as the difference between the minimum value of
# the black-box function observed so far and the true global minimum of the black box
# function. Mathematically, at time $t$, with observations $\mathcal{D}_t$, for function
# $f$ with global minimum $f^*$, the regret is defined as:
#
# $$\text{regret}_t = \min_{\mathbf{x} \in \mathcal{D_t}}f(\mathbf{x}) - f^*$$
#
# We'll then take the mean and standard deviation of the log of the regret values across
# the 5 experiments.


# %%
def obtain_log_regret_statistics(
    experiment_results: List[gpx.Dataset],
    global_minimum: ScalarFloat,
) -> Tuple[Float[Array, "N 1"], Float[Array, "N 1"]]:
    log_regret_results = []
    for exp_result in experiment_results:
        observations = exp_result.y
        cumulative_best_observations = jax.lax.associative_scan(
            jax.numpy.minimum, observations
        )
        regret = cumulative_best_observations - global_minimum
        log_regret = jnp.log(regret)
        log_regret_results.append(log_regret)

    log_regret_results = jnp.array(log_regret_results)
    log_regret_mean = jnp.mean(log_regret_results, axis=0)
    log_regret_std = jnp.std(log_regret_results, axis=0)
    return log_regret_mean, log_regret_std


bo_log_regret_mean, bo_log_regret_std = obtain_log_regret_statistics(
    bo_experiment_results, -1.031625
)
(
    random_log_regret_mean,
    random_log_regret_std,
) = obtain_log_regret_statistics(random_experiment_results, -1.031625)

# %% [markdown]
# Now, when we plot the mean and standard deviation of the log regret at each iteration,
# we can see that BO outperforms random sampling!

# %%
fig, ax = plt.subplots()
fn_evaluations = jnp.arange(1, bo_iters + initial_sample_num + 1)
ax.plot(fn_evaluations, bo_log_regret_mean, label="Bayesian Optimisation")
ax.fill_between(
    fn_evaluations,
    bo_log_regret_mean[:, 0] - bo_log_regret_std[:, 0],
    bo_log_regret_mean[:, 0] + bo_log_regret_std[:, 0],
    alpha=0.2,
)
ax.plot(fn_evaluations, random_log_regret_mean, label="Random Search")
ax.fill_between(
    fn_evaluations,
    random_log_regret_mean[:, 0] - random_log_regret_std[:, 0],
    random_log_regret_mean[:, 0] + random_log_regret_std[:, 0],
    alpha=0.2,
)
ax.axvline(x=initial_sample_num, linestyle=":")
ax.set_xlabel("Number of Black-Box Function Evaluations")
ax.set_ylabel("Log Regret")
ax.legend()
plt.show()

# %% [markdown]
# It can also be useful to plot the queried points over the course of a single BO run, in
# order to gain some insight into how the algorithm queries the search space. Below
# we do this for the first BO experiment, and can see that the algorithm initially
# performs some exploration of the search space whilst it is uncertain about the black-box
# function, but it then hones in one one of the global minima of the function, as we would hope!

# %%
fig, ax = plt.subplots()
contour_plot = ax.contourf(
    x1, x2, y.reshape(x1.shape[0], x2.shape[0]), cmap=cm.coolwarm, levels=40
)
ax.scatter(
    x_star_one[0][0],
    x_star_one[0][1],
    marker="*",
    color=cols[2],
    label="Global Minimum",
    zorder=2,
)
ax.scatter(x_star_two[0][0], x_star_two[0][1], marker="*", color=cols[2], zorder=2)
ax.scatter(
    bo_experiment_results[0].X[:, 0],
    bo_experiment_results[0].X[:, 1],
    marker="x",
    color=cols[1],
    label="Bayesian Optimisation Queries",
)
ax.set_xlabel("x1")
ax.set_ylabel("x2")
fig.colorbar(contour_plot)
ax.legend()
plt.show()

# %% [markdown]
# ### Other Acquisition Functions and Further Reading
#
# As mentioned previously, there are many acquisition functions which one may use to
# characterise the expected utility of querying the black-box function at a given point.
# We list two of the most popular below:
#
# - **Probability of Improvement (PI)** ([Kushner, 1964](https://asmedigitalcollection.asme.org/fluidsengineering/article/86/1/97/392213/A-New-Method-of-Locating-the-Maximum-Point-of-an)): Given the lowest objective function observation
#   so far, $f(\mathbf{x}^*)$, PI calculates the probability that the objective function's
#   value at a given point $\mathbf{x}$ is lower than $f(\mathbf{x}^*)$. Given a GP
#   surrogate model $\mathcal{M}_i$, PI is defined mathematically as:
#   $$
#   \alpha_{\text{PI}}(\mathbf{x}; \mathcal{D}_i, \mathcal{M}_i) = \mathbb{P}[\mathcal{M}_i (\mathbf{x}) < f(\mathbf{x}^*)] = \Phi \left(\frac{f(\mathbf{x}^*) - \mu_{\mathcal{M}_i}(\mathbf{x})}{\sigma_{\mathcal{M}_i}(\mathbf{x})}\right)
#   $$
#
#   with $\Phi(\cdot)$ denoting the standard normal cumulative distribution function.
#
# - **Expected Improvement (EI)** ([Močkus, 1974](https://link.springer.com/chapter/10.1007/3-540-07165-2_55)) - EI goes beyond PI by not only considering the
#   probability of improving on the current best observed point, but also taking into
#   account the \textit{magnitude} of improvement. Mathematically, this is defined as
#   follows:
#   $$
#   \begin{aligned}
#   \alpha_{\text{EI}}(\mathbf{x};\mathcal{D}_i, \mathcal{M}_i) &= \mathbb{E}[(f(\mathbf{x}^*) - \mathcal{M}_i(\mathbf{x}))\mathbb{I}(\mathcal{M}_i(\mathbf{x}) < f(\mathbf{x}^*))] \\
#   &= \underbrace{(f(\mathbf{x}^*) - \mu_{\mathcal{M}_i}(\mathbf{x}))\Phi
#   \left(\frac{f(\mathbf{x}^*) -
#   \mu_{\mathcal{M}_i}(\mathbf{x})}{\sigma_{\mathcal{M}_i}(\mathbf{x})}\right)}_\text{exploits
#   areas with low mean} \\
#   &+  \underbrace{\sigma_{\mathcal{M}_i}(\mathbf{x}) \phi \left(\frac{f(\mathbf{x}^*) - \mu_{\mathcal{M}_i}(\mathbf{x})}{\sigma_{\mathcal{M}_i}(\mathbf{x})}\right)}_\text{explores areas with high variance} \nonumber
#   \end{aligned}
#   $$
#
#   with $\mathbb{I}(\cdot)$ denoting the indicator function and $\phi(\cdot)$ being the
#   standard normal probability density function.
#
# For those particularly interested in diving deeper into Bayesian optimisation, be sure
# to check out Shahriari et al.'s "[Taking the Human Out of the Loop:
# A Review of Bayesian
# Optimization](https://www.cs.ox.ac.uk/people/nando.defreitas/publications/BayesOptLoop.pdf)",
# which includes a wide variety of acquisition functions, as well as some examples of more
# exotic BO problems, such as problems which also feature unknown constraints.
#
# ## System Configuration

# %%
# %reload_ext watermark
# %watermark -n -u -v -iv -w -a 'Thomas Christie'
