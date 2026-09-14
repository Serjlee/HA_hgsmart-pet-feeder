# Standalone sound-upload test

`upload_sound.py` exercises the custom-sound workflow without Home Assistant.
It deliberately neither exposes nor calls a feeding operation.

Create an isolated environment and install the one Python dependency:

```shell
python3 -m venv .venv
.venv/bin/pip install -r tools/requirements.txt
```

Provide credentials through environment variables so they do not appear in the
shell history or process list:

```shell
export HGSMART_USERNAME='account@example.com'
export HGSMART_PASSWORD='your-password'
.venv/bin/python tools/upload_sound.py /path/to/sound.mp3
```

Pass `--volume 25` to use 25% of the source amplitude. The default is `100`.
To install the precomputed shortest valid silent WAV instead, omit the audio
path and pass `--silence`.

`HGSMART_REFRESH_TOKEN` can replace `HGSMART_PASSWORD`. If several supported
feeders are attached to the account, pass `--device-id`. If the API does not
publish the local address, pass `--host 192.168.1.42`; TCP port 3333 is assumed.

For an interactive local test, credentials can instead be placed in a
permission-restricted JSON file and passed with `--credentials-file`:

```json
{"username": "account@example.com", "password": "your-password"}
```

Compatible PCM WAV files (16-bit, mono, 22050 Hz) can be sent directly. Other
audio files require `ffmpeg` on `PATH`; they are converted and trimmed to ten
seconds before upload. Run `--help` for all options.
