"""Existing business regression tests predate authentication.

Compatibility applies only while a pytest test is active; new security tests
explicitly disable it to verify production-default authentication behavior.
"""
import os
os.environ['ZD_AUTH_TEST_COMPAT']='1'
