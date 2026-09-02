# Adaptive Energy Tuning

Energy Spectrum and RF Phase-Energy Gain Scan both adjust an energy-related
actuator while observing a dispersive screen. Their adaptive scan is organized
as a measurement layer plus composable tuning stages.

## Measurement Layer

The measurement layer is not an optimization goal. It supplies the data used by
the stages:

- acquire one or more screen images;
- apply the configured image orientation, background, and ROI;
- reject invalid or unstable frames;
- calculate a brightness metric;
- fit the horizontal beam profile and obtain its center;
- report fit quality and frame statistics.

The shared measurement contract is implemented in
`src/shared/energy_tuning/measurement.py`. Applications provide the image and
profile-fitting details because EPICS, camera geometry, and profile libraries
are application/runtime concerns.

## Brightness Peak Stage

`brightness_peak` does not mean finding the brightest pixel in one image. It
means finding the energy setting with the largest reliable beam-brightness
metric inside a configured scan window.

The stage may include:

1. a coarse scan to locate the valid beam region;
2. a fine scan over that region;
3. multi-frame sampling and stability checks at each point;
4. selection of the maximum stable brightness value.

The selected point is a useful seed, but it does not necessarily put the beam
center at `x_reference_mm`. A beam can be brightest while still being offset
from the calibrated reference position.

### RF Phase-Energy Gain Scan

RF uses the same brightness objective with a different exploration strategy:
`strategy: center_outward` starts at the current energy and orders the configured
candidate points from the center toward both sides of the allowed window. The
`points` value is the total number of candidate energies, not the energy step.
Among all valid candidates, the stage still selects the largest reliable
brightness as the seed for `center_lock`.

## Center Lock Stage

`center_lock` adjusts the energy setting so that the fitted beam center reaches
`x_reference_mm` within the configured tolerance.

At each correction point it:

1. measures and fits the horizontal profile;
2. calculates `center_offset_mm = center_mm - x_reference_mm`;
3. predicts a new energy using recent measurements and the configured
   dispersion relation or correction limits;
4. repeats until the offset is within tolerance or the configured iteration and
   range limits are reached;
5. performs a final multi-frame verification.

This stage optimizes position, not brightness. It normally starts from the
current energy or from the result of `brightness_peak`.

## Default Combination

The recommended default is:

```json
{"pipeline": ["brightness_peak", "center_lock"]}
```

The execution is therefore:

```text
screen measurement -> brightness peak seed -> center lock -> verification
```

The stages can also be selected individually:

```json
{"pipeline": ["brightness_peak"]}
```

```json
{"pipeline": ["center_lock"]}
```

`center_lock`-only operation requires a meaningful starting actuator value.

## Configuration Shape

New workflows keep shared sampling settings in `measurement` and stage-owned
settings in `stages`:

```json
{
  "pipeline": ["brightness_peak", "center_lock"],
  "measurement": {},
  "stages": {
    "brightness_peak": {"strategy": "coarse_fine"},
    "center_lock": {"strategy": "local_step"}
  }
}
```

RF Phase-Energy Gain Scan uses the same shape, with `center_outward` and
`secant_dispersion` as its stage strategies. `algorithm` is intentionally not
a top-level field: search behavior belongs to the stage that owns it.

The resolver accepts previous top-level stage keys and legacy objective or
pipeline keys for backward compatibility. New configurations should use
`stages`.

## Compatibility

The legacy objective name `brightness_then_profile_lock` maps to:

```json
["brightness_peak", "center_lock"]
```

Legacy objective names remain accepted while new workflows should use the
explicit pipeline form.
