from enum import Enum


class DeployStep(Enum):
    INIT = "INIT"
    DEV = "DEV"
    STG = "STG"
    PRD = "PRD"
