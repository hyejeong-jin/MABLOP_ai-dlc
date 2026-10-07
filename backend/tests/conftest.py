"""pytest config: put backend package root on sys.path for test imports."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
