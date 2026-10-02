"""What each kind of speaker can reproduce, from its maker's specification.

The calibration microphone is a cheap USB one, and a single measurement in a room reads
lower than the speaker really is at both ends of the band. The EQ is therefore
**optimistic**: when the person says which speaker it is, the maker's band is trusted over
the measurement, and the EQ may lift anything inside it. Without a profile, the measured
band is widened by an octave on each side.

Sources (REPORTADO, the maker's figures, not measured here):
- JBL Go 4: 90 Hz - 20 kHz, 4.2 W RMS (JBL Go 4 spec sheet,
  https://www.jbl.com/GO-4.html).
- JBL Charge 6: 56 Hz - 20 kHz at -6 dB, 45 W (speakerranking.com, quoting the maker;
  https://www.speakerranking.com/compare/jbl-charge-6-vs-jbl-charge-4/).
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SpeakerProfile:
    key: str
    label: str
    low_hz: float | None
    high_hz: float | None


PROFILES: dict[str, SpeakerProfile] = {
    "generic": SpeakerProfile("generic", "Genérico", None, None),
    "go4": SpeakerProfile("go4", "JBL Go 4", 90.0, 20000.0),
    "charge6": SpeakerProfile("charge6", "JBL Charge 6", 56.0, 20000.0),
}

LOWEST_HZ = 60.0
"""Even optimistic, nothing below this is lifted without a profile that says the speaker
reaches it: a small driver asked for deep bass only moves more."""


def guess(name: str) -> str | None:
    """A profile from the device's name, when it says it plainly ("JBL Go 4 Red")."""
    lowered = name.lower().replace(" ", "")
    for key, needle in (("go4", "go4"), ("charge6", "charge6")):
        if needle in lowered:
            return key
    return None


def boost_band(kind: str | None, measured: tuple[float | None, float | None]) -> tuple[float, float]:
    """The band the EQ may lift: the profile's, or the measured one widened by an octave."""
    profile = PROFILES.get(kind or "")
    if profile is not None and profile.low_hz is not None and profile.high_hz is not None:
        return profile.low_hz, profile.high_hz
    low, high = measured
    return (max(LOWEST_HZ, (low or 100.0) / 2), min(20000.0, (high or 10000.0) * 2))
