# HGSmart custom sound protocol

This document records the reverse engineering used by the `hgsmart.upload_sound`
action. It is intended to make the implementation reviewable and to separate
observations from implementation details that remain inferred.

## Reference application

- Android package: `net.hgsmart.iot`
- Store: [HGsmart on Google Play](https://play.google.com/store/apps/details?id=net.hgsmart.iot)
- Analysed release: 1.0.27 (version code 170), published 2026-07-12
- XAPK SHA-256: `b2b86382dce6f3ae3174f8848330bd918dfa858c9402a72c8a4b0d84c667a9eb`
- Base APK SHA-256: `90f640b0b148ff9812f5a05cf282eb63b78aa1232974f619c8cf93aa0284bfa4`
- ARM64 split SHA-256: `2ab9f531eeb1c5170ddbc0f08835ab44edbb7d79ccfbcc9e21b028bd3f74e57b`

The APK certificate SHA-256 fingerprint is
`07:01:BE:38:42:8D:CE:3F:B7:2E:7E:92:D5:81:79:3A:E2:4E:4E:45:C1:AF:61:E6:B3:95:02:25:D9:9D:D1:5E`.
The Flutter AOT snapshot was inspected with
[Blutter](https://github.com/worawit/blutter).

## Confirmed sequence

1. Convert the selected recording to the format observed in a successful S30D
   transfer. The integration also enforces the official UI's ten-second limit:

   ```text
   ffmpeg -y -i INPUT -ar 22050 -ac 1 -sample_fmt s16 -acodec pcm_s16le -t 10 OUTPUT.wav
   ```

   An optional amplitude filter is applied during this conversion. `100%`
   leaves the source level unchanged; values above `100%` also use an output
   limiter to avoid digital clipping.

2. Send `POST /app/device/uploadVoiceFile` as multipart form data. The file
   field is named `voiceFile`; the app also sends the target `deviceId` as a
   plain form field. A successful response contains the platform URL.
3. Send the URL through the normal device control channel with identifier
   `getmusic`.
4. Send `music=1` and wait 500 ms. This makes the feeder open its temporary
   local listener.
5. Connect to the feeder's local endpoint. The app defaults to TCP port 3333
   when no port is supplied. An S25D was observed opening the listener only a
   few seconds after `music=1`, and briefly, so the integration retries refused
   connections for up to 20 seconds and re-sends `music=1` every 4 seconds
   while it waits.
6. Append the UTF-8 bytes `DSY-AUDIO` to the complete WAV byte sequence, split
   that framed payload into 4,096-byte chunks, and write them in order. The app
   pauses 50 ms after every chunk.
7. Flush the socket and wait up to 20 seconds for a UTF-8 response containing
   `完成接收` ("reception completed"). A TCP connection alone is not proof that
   the feeder accepted the file.
8. Send `music=0` to leave transfer mode, then close the socket.
9. Select the custom recording with `choosevoice=1`.

The resulting file is RIFF/WAVE, signed little-endian PCM (`WAVE_FORMAT_PCM`,
format tag 1), mono, 22050 Hz, 16 bits per sample. The integration rejects files
larger than 512 KiB as a defensive ceiling.

## Silent meal call

The `hgsmart.mute_meal_call` action transfers a precomputed 46-byte WAV at 0%
amplitude. Its data chunk contains one 16-bit zero sample, so its nominal
duration is 1/22050 second (about 45 microseconds). A truly empty data chunk is
not used because the transfer validator and some firmware audio decoders reject
empty audio streams.

This exact 46-byte payload was accepted and activated by an S30D. A close-range
listening check at the feeder speaker found no audible output.

## Local endpoint

`DSY-AUDIO` is the required end marker for the TCP audio payload, not a LAN
discovery message. The current integration locates the TCP endpoint as follows:

1. looks for common IP fields (`ip`, `localIp`, `deviceIp`, `ipAddress`,
   `audioHost`, or `audioAddress`) in the device and attribute responses; then
2. uses the optional `host` action field when the firmware does not expose one.

An S25D publishes the endpoint as `ip` in the `/app/device/attribute`
response, already including the port (for example `"ip":"192.168.1.42:3333"`).

The action must run while Home Assistant and the feeder are reachable on the
same LAN. `host` accepts an address with an optional port, such as
`192.168.1.42` or `192.168.1.42:3333`.

## Confidence and hardware validation

The cloud endpoint, multipart name, control identifiers, payload terminator,
4 KiB chunking, delays, acknowledgement, TCP default port, and socket timeout
were recovered from app 1.0.27. A later packet capture of a successful official
app transfer to an S30D established the actual device payload: a 494,670-byte,
11.22-second PCM 16-bit mono WAV at 22050 Hz, followed by `DSY-AUDIO`. The feeder
answered with `完成接收!\0`.

The AOT snapshot also contains an 8000 Hz G.711 A-law FFmpeg command, but the
hardware capture proves that it is not the format sent by this S30D workflow.
The earlier implementation incorrectly associated that command with meal-call
uploads. Separately, omitting the `DSY-AUDIO` terminator can leave the feeder
waiting in transfer mode.

A separate S25D experiment found a third FFmpeg recipe in `libapp.so`:
`-ar 16000 -ab 32000 -ac 1 -acodec pcm_u8` (PCM 8-bit unsigned, 16 kHz). That
experiment sent the WAV without the `DSY-AUDIO` terminator, so it neither
confirms nor rules out this format. If an S25-series feeder acknowledges the
22050 Hz 16-bit payload but does not play it, this recipe is the next candidate.

Automated tests cover WAV validation, endpoint parsing, FFmpeg command
construction, transfer-mode controls, exact framed TCP transfer, and mandatory
acknowledgement. Two earlier S30D experiments are not playback validations: the
first omitted the terminator, and the second used the wrong A-law format even
though the feeder acknowledged its framing. No feed command was issued during
these experiments or the reference capture.
