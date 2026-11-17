from collatz.steps import collatz_sequence


def test_sequence_of_six():
    assert collatz_sequence(6) == [6, 3, 10, 5, 16, 8, 4, 2, 1]
