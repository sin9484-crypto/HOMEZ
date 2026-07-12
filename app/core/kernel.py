"""
HOMEZ AI Commerce OS
Kernel Engine
"""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class KernelState:
    initialized: bool = False
    started_at: datetime | None = None


class HomezKernel:

    def __init__(self):
        self.state = KernelState()
        self.services = {}

    def register(self, name: str, service):
        self.services[name] = service

    def get(self, name: str):
        return self.services.get(name)

    def boot(self):
        self.state.initialized = True
        self.state.started_at = datetime.now()

        print("========================================")
        print("HOMEZ Kernel Boot")
        print(f"Services : {len(self.services)}")
        print(f"Started  : {self.state.started_at}")
        print("========================================")


kernel = HomezKernel()