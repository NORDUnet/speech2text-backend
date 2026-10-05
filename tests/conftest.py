# Copyright (c) 2025-2026 Sunet.
# Contributor: Kristofer Hallin
#
# This file is part of Sunet Scribe.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import sys
from pathlib import Path

# Add project root to Python path
sys.path.insert(0, str(Path(__file__).parent.parent))


# Unit tests must not start the notification module's recurring background timer.
# Without this, importing db.job leaves pytest alive after the tests finish.
import threading
from unittest.mock import patch


def pytest_sessionstart(session):
    session._notification_timer_patch = patch.object(threading.Timer, "start")
    session._notification_timer_patch.start()


def pytest_sessionfinish(session, exitstatus):
    session._notification_timer_patch.stop()
