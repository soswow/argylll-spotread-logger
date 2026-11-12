# spotread-logger

Polls a spectrometer or colorimeter through ArgyllCMS `spotread` and appends every reading to a CSV file, with a timestamp. It has been used with an X-Rite ColorMunki. The parser reads `spotread`'s text, so another instrument that prints the same report can be logged the same way.

`spotread` is interactive: it may wait for a calibration key, then waits for a measurement key. This tool drives those prompts on a pseudo-terminal, asks you to set the instrument, then takes a reading on a timer. With `--no-calibration` it passes `-N`. When `spotread` still has a valid calibration it goes straight to measuring; when it asks anyway, the logger waits for the calibration step.

Each row contains the measurement mode, the instrument banner, the spectrum samples, XYZ, and D50 Lab. Ambient rows also have lux, suggested EV, CCT, CRI, and TM-30. Emissive and telephoto rows include CCT, CRI, and TM-30 when `spotread` is run with `-T`, which is the default for those modes. Reflectance rows include ISO and Status A/M/T/E density when `-d` is set, which is the default for that mode. Values `spotread` prints as `nan`, and a missing Planckian temperature, are stored as empty cells. The original `spotread` text is not written to the file. The spectrum is one JSON array of the sample values from the reported start wavelength to the reported end wavelength. Reflectance samples are percents. Ambient, emissive, and telephoto samples are absolute spectral power; the [spotread manual](https://www.argyllcms.com/doc/spotread.html) lists the units. Reflectance XYZ is on a 0–100 scale. The light modes report XYZ in cd/m².

## Install

Requires Python 3.11 or newer and ArgyllCMS `spotread` on your `PATH`.

ArgyllCMS builds and per-platform install notes, including the macOS USB setup, are at [argyllcms.com](https://www.argyllcms.com/). macOS binaries are on the [macOS download page](https://www.argyllcms.com/downloadmac.html). On macOS, Homebrew works too:

```bash
brew install argyll-cms
```

That formula provides `spotread`. This project was developed against ArgyllCMS 3.5.0 installed that way.

Run it from this directory. Python picks up the `spotread_logger` package on the path, so the project itself does not need to be installed. `pexpect` does.

```bash
cd spotread-logger
python3 -m venv .venv
source .venv/bin/activate
pip install pexpect
```

## Use

Put the instrument in the calibration position when asked. On a ColorMunki that is the calibration dial position. Press Enter, set it for the mode you chose, then press Enter again. Readings are appended after that until you press Ctrl-C.

| Mode | Default `spotread` arguments | Where the sensor goes |
| --- | --- | --- |
| `ambient` | `-a -H -s -v` | Ambient diffuser. Illuminance, CCT, CRI, TM-30. |
| `reflectance` | `-H -s -v -d` | Aperture down on the sample, so the instrument lamp lights it. Adds density. |
| `emissive` | `-e -H -s -v -T` | Open aperture aimed at a display or lamp. `-T` adds CCT, CRI, and TM-30. |
| `telephoto` | `-p -H -s -v -T` | Narrow aperture aimed at the source. On a ColorMunki this is the projector position. Same report shape as emissive. |

```bash
python -m spotread_logger --interval 30 --output readings.csv
python -m spotread_logger --mode reflectance --output paper.csv
python -m spotread_logger --mode emissive --output lamp.csv
python -m spotread_logger --mode telephoto --no-calibration --output spot.csv
```

| Option | Default | Meaning |
| --- | --- | --- |
| `-i, --interval <seconds>` | `30` | Wait between readings. `min` (or `0`) reads again as soon as the previous one is logged. |
| `-o, --output <file>` | `readings.csv` | CSV to append. The header is written only when the file is empty. Use a new file after this column set changed: older files keep their old header. |
| `--mode <name>` | `ambient` | `ambient`, `reflectance`, `emissive`, or `telephoto`. Sets the default `spotread` arguments, the setup prompt, and the `measurement_mode` column. |
| `--spotread <command>` | `spotread` | Executable name or path. |
| `--timeout <seconds>` | `120` | How long to wait for connect, calibration, or a single reading. |
| `--no-prompt` | off | Send the calibration key and start measuring without waiting for Enter. |
| `--no-calibration` | off | Pass `-N`. `spotread` skips calibration when a previous one is still valid. |
| `[spotread-args...]` | from `--mode` | Replaces the mode's arguments. `--no-calibration` still appends `-N` when it is missing. |

```bash
python -m spotread_logger --interval 10 --output ~/light.csv -- -a -H -s -v
```

Ctrl-C sends `q` to `spotread` so the instrument is released.

## License

Copyright 2026 Aleksandr Motsjonov. Licensed under [GPL-3.0](LICENSE).

## Develop

```bash
pip install pytest ruff
pytest
ruff check spotread_logger tests
```
