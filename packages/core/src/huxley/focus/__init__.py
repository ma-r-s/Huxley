"""Focus management — named-channel arbitration over a single audio resource.

The public surface is built up as the package fills in. Today the
vocabulary is importable; `FocusManager` arrives in the same commit.
"""

from huxley.focus.manager import FocusManager
from huxley.focus.vocabulary import (
    CHANNEL_PRIORITY,
    Activity,
    Channel,
    ChannelObserver,
    ContentType,
    FocusState,
    MixingBehavior,
    mixing_for_background,
)

__all__ = [
    "CHANNEL_PRIORITY",
    "Activity",
    "Channel",
    "ChannelObserver",
    "ContentType",
    "FocusManager",
    "FocusState",
    "MixingBehavior",
    "mixing_for_background",
]
