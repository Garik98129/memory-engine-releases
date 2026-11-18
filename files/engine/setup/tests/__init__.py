# F1: the tests never reach the network. The watch of an installation without a bot reads the
# repository of releases (engine.checks.min_engine); here it reads nothing unless a test points it
# at a bare repository of its own.
from engine.checks import min_engine

min_engine.ORIGIN = lambda repository: "/nonexistent/repository-of-releases-in-the-tests"
