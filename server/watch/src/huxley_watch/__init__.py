"""huxley-watch: the caregiver's view of a Huxley deployment (T1.42).

Its own process. It reads the server's logs and the persona's database,
never writes them, turns them into a few plain "needs you" sentences,
serves them as one page, and pushes the same sentences as alerts once
when they appear and once when they clear.
"""

__version__ = "0.1.0"
