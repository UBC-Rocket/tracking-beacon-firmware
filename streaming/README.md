# Streaming Ground Station

## Setup guide
Check [Confluence](https://ubcrocket.atlassian.net/wiki/x/IQBACQ)

## Testing with Simulated Telemetry

The `generate_sample_stream` script (in `ubcrocket-gs-lite/`) produces fake COBS-encoded telemetry packets with a realistic flight profile. Use it to test the streaming overlays without a radio link.

To use the fake data:

### install
- `socat` installed (`brew install socat` on macOS)
- Dependencies installed in `ubcrocket-gs-lite/` (`cd ubcrocket-gs-lite && uv sync`)

### setup

**1. Create a virtual serial pair**

```bash
socat pty,raw,echo=0,link=/tmp/telem_rx pty,raw,echo=0,link=/tmp/telem_tx
```
- Replace link with /tmp/telem_tx with your usb port
=> 2 linked virtual serial ports. The streaming app reads from the usb port you specify, and you write sample data to the usb port.

**2. Stream sample packets**

In a second terminal:

```bash
cd ubcrocket-gs-lite
uv run python -m simple_gs.generate_sample_stream -n 500 --rate 20 > /tmp/telem_tx
```
- Replace link with /tmp/telem_tx with your usb port
This sends 500 packets at 20 Hz with a fake flight profile (standby, ascent, mach lock, drogue descent, main descent, landed).

**3. Start the ground station**

In a third terminal:

```bash
cd streaming
python main.py
```

### generate_sample_stream Options
There are also some useful cli commands to customize the sample data / save it

 `-n`, `--count`: Number of packets to generate (default: 10)
`-o`, `--output FILE`: Write raw binary stream to a file
`--hex`: Print hex representation to stdout
`--rate HZ`: Stream at a fixed rate (e.g. 20 for 20 Hz). Default: 0 (all at once)