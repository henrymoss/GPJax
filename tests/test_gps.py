# Copyright 2022 The GPJax Contributors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ==============================================================================

import typing as tp

import jax
import distrax as dx
import jax.numpy as jnp
import jax.random as jr
import pytest
from jax.config import config

from gpjax import Dataset, initialise
from gpjax.gps import (
    AbstractPrior,
    AbstractPosterior,
    ConjugatePosterior,
    NonConjugatePosterior,
    Prior,
    construct_posterior,
)
from gpjax.mean_functions import Zero, Constant
from jaxkern import RBF, Matern12, Matern32, Matern52
from gpjax.likelihoods import Bernoulli, Gaussian
from gpjax.parameters import ParameterState

# Enable Float64 for more stable matrix inversions.
config.update("jax_enable_x64", True)
NonConjugateLikelihoods = [Bernoulli]


@pytest.mark.parametrize("num_datapoints", [1, 10])
def test_prior(num_datapoints):
    p = Prior(kernel=RBF())
    parameter_state = initialise(p, jr.PRNGKey(123))
    params, _, _ = parameter_state.unpack()
    assert isinstance(p, Prior)
    assert isinstance(p, AbstractPrior)
    prior_rv_fn = p(params)
    assert isinstance(prior_rv_fn, tp.Callable)

    x = jnp.linspace(-3.0, 3.0, num_datapoints).reshape(-1, 1)
    predictive_dist = prior_rv_fn(x)
    assert isinstance(predictive_dist, dx.Distribution)
    mu = predictive_dist.mean()
    sigma = predictive_dist.covariance()
    assert mu.shape == (num_datapoints,)
    assert sigma.shape == (num_datapoints, num_datapoints)


@pytest.mark.parametrize("num_datapoints", [1, 5])
@pytest.mark.parametrize("kernel", [RBF(), Matern52()])
@pytest.mark.parametrize("mean_function", [Zero(), Constant()])
def test_prior_sample_approx(num_datapoints, kernel, mean_function):
    p = Prior(kernel=kernel, mean_function=mean_function)
    key = jr.PRNGKey(123)
    parameter_state = initialise(p, key)
    params, _, _ = parameter_state.unpack()
    params["kernel"]["lengthscale"]=5.0
    params["kernel"]["variance"]=0.1
    
    with pytest.raises(ValueError):
        p.sample_approx(-1,params, key)
    with pytest.raises(ValueError):
        p.sample_approx(0.5,params, key)
    with pytest.raises(ValueError):
        p.sample_approx(1,params, key, -10)
    with pytest.raises(ValueError):
        p.sample_approx(1,params, key, 0.5)

    sampled_fn = p.sample_approx(1,params, key, 100)
    assert isinstance(sampled_fn, tp.Callable) # check type

    x = jnp.linspace(-3.0, 3.0, num_datapoints).reshape(-1, 1)
    evals = sampled_fn(x)
    assert evals.shape == (num_datapoints, 1.0) # check shape

    sampled_fn_2 = p.sample_approx(1,params, key, 100)
    evals_2 = sampled_fn_2(x)
    max_delta = jnp.max(jnp.abs(evals - evals_2))
    assert max_delta == 0.0 # samples same for same seed

    new_key = jr.PRNGKey(12345)
    sampled_fn_3 = p.sample_approx(1,params, new_key, 100)
    evals_3 = sampled_fn_3(x)
    max_delta = jnp.max(jnp.abs(evals - evals_3))
    assert max_delta > 0.1 # samples different for different seed

    # Check validty of samples using Monte-Carlo
    sampled_fn = p.sample_approx(10_000,params, key, 100)
    sampled_evals = sampled_fn(x)
    approx_mean = jnp.mean(sampled_evals, -1) 
    approx_var = jnp.var(sampled_evals, -1)
    true_predictive = p(params)(x)
    true_mean = true_predictive.mean()
    true_var = jnp.diagonal(true_predictive.covariance())
    max_error_in_mean = jnp.max(jnp.abs(approx_mean - true_mean))
    max_error_in_var = jnp.max(jnp.abs(approx_var - true_var))
    assert max_error_in_mean < 0.02 # check that samples are correct
    assert max_error_in_var < 0.05 # check that samples are correct


@pytest.mark.parametrize("num_datapoints", [1, 2, 10])
def test_conjugate_posterior(num_datapoints):
    key = jr.PRNGKey(123)
    x = jnp.sort(
        jr.uniform(key=key, minval=-2.0, maxval=2.0, shape=(num_datapoints, 1)),
        axis=0,
    )
    y = jnp.sin(x) + jr.normal(key=key, shape=x.shape) * 0.1
    D = Dataset(X=x, y=y)
    # Initialisation
    p = Prior(kernel=RBF())
    lik = Gaussian(num_datapoints=num_datapoints)
    post = p * lik
    assert isinstance(post, ConjugatePosterior)
    assert isinstance(post, AbstractPrior)
    assert isinstance(p, AbstractPrior)

    post2 = lik * p
    assert isinstance(post2, ConjugatePosterior)
    assert isinstance(post2, AbstractPrior)

    parameter_state = initialise(post, key)
    params, *_ = parameter_state.unpack()

    # Marginal likelihood
    mll = post.marginal_log_likelihood(train_data=D)
    objective_val = mll(params)
    assert isinstance(objective_val, jax.Array)
    assert objective_val.shape == ()

    # Prediction
    predictive_dist_fn = post(params, D)
    assert isinstance(predictive_dist_fn, tp.Callable)

    x = jnp.linspace(-3.0, 3.0, num_datapoints).reshape(-1, 1)
    predictive_dist = predictive_dist_fn(x)
    assert isinstance(predictive_dist, dx.Distribution)

    mu = predictive_dist.mean()
    sigma = predictive_dist.covariance()
    assert mu.shape == (num_datapoints,)
    assert sigma.shape == (num_datapoints, num_datapoints)


@pytest.mark.parametrize("num_datapoints", [1, 2, 10])
@pytest.mark.parametrize("likel", NonConjugateLikelihoods)
def test_nonconjugate_posterior(num_datapoints, likel):
    key = jr.PRNGKey(123)
    x = jnp.sort(
        jr.uniform(key=key, minval=-2.0, maxval=2.0, shape=(num_datapoints, 1)),
        axis=0,
    )
    y = 0.5 * jnp.sign(jnp.cos(3 * x + jr.normal(key, shape=x.shape) * 0.05)) + 0.5
    D = Dataset(X=x, y=y)
    # Initialisation
    p = Prior(kernel=RBF())
    lik = likel(num_datapoints=num_datapoints)
    post = p * lik
    assert isinstance(post, NonConjugatePosterior)
    assert isinstance(post, AbstractPrior)
    assert isinstance(p, AbstractPrior)

    parameter_state = initialise(post, key)
    params, _, _ = parameter_state.unpack()
    assert isinstance(parameter_state, ParameterState)

    # Marginal likelihood
    mll = post.marginal_log_likelihood(train_data=D)
    objective_val = mll(params)
    assert isinstance(objective_val, jax.Array)
    assert objective_val.shape == ()

    # Prediction
    predictive_dist_fn = post(params, D)
    assert isinstance(predictive_dist_fn, tp.Callable)

    x = jnp.linspace(-3.0, 3.0, num_datapoints).reshape(-1, 1)
    predictive_dist = predictive_dist_fn(x)
    assert isinstance(predictive_dist, dx.Distribution)

    mu = predictive_dist.mean()
    sigma = predictive_dist.covariance()
    assert mu.shape == (num_datapoints,)
    assert sigma.shape == (num_datapoints, num_datapoints)


@pytest.mark.parametrize("num_datapoints", [1, 10])
@pytest.mark.parametrize("lik", [Bernoulli, Gaussian])
def test_param_construction(num_datapoints, lik):
    p = Prior(kernel=RBF()) * lik(num_datapoints=num_datapoints)
    parameter_state = initialise(p, jr.PRNGKey(123))
    params, _, _ = parameter_state.unpack()

    if isinstance(lik, Bernoulli):
        assert sorted(list(params.keys())) == [
            "kernel",
            "latent_fn",
            "likelihood",
            "mean_function",
        ]
    elif isinstance(lik, Gaussian):
        assert sorted(list(params.keys())) == [
            "kernel",
            "likelihood",
            "mean_function",
        ]


@pytest.mark.parametrize("lik", [Bernoulli, Gaussian])
def test_abstract_posterior(lik):
    pr = Prior(kernel=RBF())
    likelihood = lik(num_datapoints=10)

    with pytest.raises(TypeError):
        _ = AbstractPosterior(pr, likelihood)

    class DummyPosterior(AbstractPosterior):
        def predict(self):
            pass

    dummy_post = DummyPosterior(pr, likelihood)
    assert isinstance(dummy_post, AbstractPosterior)
    assert dummy_post.likelihood == likelihood
    assert dummy_post.prior == pr


@pytest.mark.parametrize("lik", [Bernoulli, Gaussian])
def test_posterior_construct(lik):
    pr = Prior(kernel=RBF())
    likelihood = lik(num_datapoints=10)
    p1 = pr * likelihood
    p2 = construct_posterior(prior=pr, likelihood=likelihood)
    assert type(p1) == type(p2)


@pytest.mark.parametrize("kernel", [RBF(), Matern12(), Matern32(), Matern52()])
def test_initialisation_override(kernel):
    key = jr.PRNGKey(123)
    override_params = {"lengthscale": jnp.array([0.5]), "variance": jnp.array([0.1])}
    p = Prior(kernel=kernel) * Gaussian(num_datapoints=10)
    parameter_state = initialise(p, key, kernel=override_params)
    ds = parameter_state.unpack()
    for d in ds:
        assert "lengthscale" in d["kernel"].keys()
        assert "variance" in d["kernel"].keys()
    assert ds[0]["kernel"]["lengthscale"] == jnp.array([0.5])
    assert ds[0]["kernel"]["variance"] == jnp.array([0.1])

    with pytest.raises(ValueError):
        parameter_state = initialise(p, key, keernel=override_params)




