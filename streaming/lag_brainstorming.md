## Brainstorming
- Running into performance issues with choppy video. The video, when it is left to run for a longer period of time, ends up becoming "choppy" jumping between frames, glitching, and speeding up at some parts.

- Taking the video from a UDP port  from gstreamer. We can see the video at a few places in this pipeline:
    1. cosmostreamer.local which directly streams the video from the cosmostreamer's server onto my laptop. There is no latency on this window.
    2. the widow I could open by running the gst_command (after modifying fdsink so instead a window pops open) directly in the terminal. This is has some lags in the video
    3. In my script, with the overlays, the lags are a lot more.
    4. The saved mp4 has even more lags I believe

- Doing a 10 second video was fine, but 3 minutes ends up becoming really bad for example.

- What could be causing this? the gst_command buffer size? How we are doing threading with overlays causing delays and dropping packets? Processing taking longer with the overlays causing udp packets to drop??

- We have a 30fps video => 1 second / 30 frames = 33.3ms per frame
So we need to make sure our processing loop takes 33.3ms across different hardware max. I think with the image processing + multithreading overhead, we are going over that time

## Reducing time from overlay processing
We have a 30fps video => 1 second / 30 frames = 33.3ms per frame
So we need to make sure our processing loop takes 33.3ms across different hardware max. I think with the image processing + multithreading overhead, we are going over that time