"""
Request-modellen voor de REST-API (Pydantic v2).
FastAPI valideert inkomende JSON hiertegen en geeft bij fouten een
duidelijke 422-response terug met veld en reden.
"""

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Procesgrootheden die een meting kan bevatten
MEASURED_FIELDS = ("CA", "T", "Q", "Tc", "UA", "F", "T_in", "CA_in")


class TwinMeasurement(BaseModel):
    """
    Eén live-meting uit de installatie. Alle velden zijn optioneel, maar er
    moet minstens één procesgrootheid in zitten.

    Voorbeeld:  {"CA": 0.5, "T": 350, "Q": -5000}
    """

    # Extra velden (bv. "tag", "quality") worden geaccepteerd en genegeerd.
    model_config = ConfigDict(extra="allow")

    CA: Optional[float] = Field(None, ge=0, le=100, description="Gemeten concentratie A [mol/L]")
    T: Optional[float] = Field(None, gt=0, le=2000, description="Gemeten temperatuur [K]")
    Q: Optional[float] = Field(None, ge=-1e7, le=1e7, description="Gemeten warmtestroom naar de reactor [kJ/min]")
    Tc: Optional[float] = Field(None, gt=0, le=2000, description="Gemeten koelmiddeltemperatuur [K]")
    UA: Optional[float] = Field(None, ge=0, le=1e6, description="Bekende warmteoverdracht UA [kJ/(min*K)]")
    F: Optional[float] = Field(None, ge=0, description="Gemeten volumestroom [L/min]")
    T_in: Optional[float] = Field(None, gt=0, le=2000, description="Gemeten voedingstemperatuur [K]")
    CA_in: Optional[float] = Field(None, ge=0, le=100, description="Gemeten voedingsconcentratie [mol/L]")
    timestamp: Optional[str] = Field(None, description="Tijdstempel van de bron (ISO-8601), informatief")

    @model_validator(mode="after")
    def at_least_one_value(self) -> "TwinMeasurement":
        if all(getattr(self, k) is None for k in MEASURED_FIELDS):
            raise ValueError("Meting bevat geen procesgrootheden (" + ", ".join(MEASURED_FIELDS) + ").")
        return self

    def process_values(self) -> Dict[str, float]:
        return {k: getattr(self, k) for k in MEASURED_FIELDS if getattr(self, k) is not None}


class TwinModeRequest(BaseModel):
    enabled: bool
    blend: Optional[float] = Field(None, gt=0, le=1, description="1 = harde sync, 0.2 = zachte bijsturing")


class ParametersUpdate(BaseModel):
    """Gedeeltelijke update; alleen opgegeven velden veranderen."""

    model_config = ConfigDict(extra="forbid")

    process: Dict[str, Any] = Field(default_factory=dict)
    initial: Dict[str, Any] = Field(default_factory=dict)
    safety: Dict[str, Any] = Field(default_factory=dict)
    control: Dict[str, Any] = Field(default_factory=dict)
    runtime: Dict[str, Any] = Field(default_factory=dict)


class InitialState(BaseModel):
    CA0: float = Field(..., ge=0)
    T0: float = Field(..., gt=0)


class SimulateRequest(BaseModel):
    """
    Offline "wat-als"-simulatie. Raakt de live-simulatie NIET aan.

    parameters         : overschrijvingen van procesparameters (optioneel)
    control            : overschrijvingen van regelaarinstellingen (optioneel)
    initial            : begintoestand (optioneel)
    from_current_state : start vanuit de actuele live-toestand incl. regelaar (voorspelling)
    """

    model_config = ConfigDict(extra="forbid")

    parameters: Dict[str, Any] = Field(default_factory=dict)
    control: Dict[str, Any] = Field(default_factory=dict)
    initial: Optional[InitialState] = None
    from_current_state: bool = False
    duration: float = Field(30.0, gt=0, le=10000, description="Simulatieduur [min]")
    dt: float = Field(0.01, gt=0, le=0.1, description="Maximale integratiestap = sampletijd regelaar [min]")
    n_points: int = Field(500, ge=2, le=5000)
    method: str = Field("rk4", pattern="^(rk4|euler)$")
