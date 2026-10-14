"""Existing business regression tests predate authentication.

Compatibility applies only while a pytest test is active; new security tests
explicitly disable it to verify production-default authentication behavior.
"""
import os
os.environ['ZD_AUTH_TEST_COMPAT']='1'

# Historical synthetic rule cases remain in isolated unit tests only.
# A live uvicorn process never inherits pytest's in-process marker.
os.environ['ZD_LEGACY_MODE']='1'
