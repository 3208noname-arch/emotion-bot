"""全部のテストを走らせる: python -m tests（各テストは import した時点で走る）。"""
from tests import affinity_test, body_test, decide_test, names_test, ratings_test  # noqa: F401
