import pytest
from app.errors import AppError
from app.adapters.embedding import validate_vectors


@pytest.mark.parametrize("vectors", [[[0.0] * 8], [[float("nan")] + [0.0] * 1023], [[0.0] * 1024], []])
def test_invalid_embedding_shape_or_values_are_rejected(vectors):
    with pytest.raises(AppError):
        validate_vectors(vectors, expected_count=1, dimension=1024)


def test_finite_nonzero_vector_is_accepted():
    vector = [1.0] + [0.0] * 1023
    assert validate_vectors([vector], expected_count=1, dimension=1024) == [vector]
