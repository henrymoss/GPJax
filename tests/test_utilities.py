from gpjax.utils import concat_dictionaries, I, merge_dictionaries, standardise, unstandardise
import jax.numpy as jnp
import jax.random as jr
import pytest


@pytest.mark.parametrize('n', [1, 10, 100])
def test_identity(n):
    identity = I(n)
    assert identity.shape == (n, n)
    assert (jnp.diag(identity) == jnp.ones(shape = (n, ))).all()


def test_concat_dict():
    d1 = {'a': 1, 'b': 2}
    d2 = {'c': 3, 'd': 4}
    d = concat_dictionaries(d1, d2)
    assert list(d.keys()) == ['a', 'b', 'c', 'd']
    assert list(d.values()) == [1, 2, 3, 4]


def test_merge_dicts():
    d1 = {'a': 1, 'b': 2}
    d2 = {'b': 3}
    d = merge_dictionaries(d1, d2)
    assert list(d.keys()) == ['a', 'b']
    assert list(d.values()) == [1, 3]


def test_standardise():
    key = jr.PRNGKey(123)
    x = jr.uniform(key, shape=(100, 1))
    xtr, xmean, xstd = standardise(x)
    assert pytest.approx(jnp.mean(xtr), rel=4) == 0.

    xtr2 = standardise(x, xmean, xstd)
    assert pytest.approx(jnp.mean(xtr2), rel=4) == 0.

    xuntr = unstandardise(xtr, xmean, xstd)
    diff = jnp.sum(jnp.abs(xuntr - x))
    assert pytest.approx(diff) == 0