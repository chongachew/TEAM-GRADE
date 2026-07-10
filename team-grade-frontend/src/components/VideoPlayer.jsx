import { forwardRef } from "react";
import { videoUrl } from "../api";

const VideoPlayer = forwardRef(function VideoPlayer({ videoId, onTimeUpdate }, ref) {
  return (
    <video
      ref={ref}
      src={videoUrl(videoId)}
      controls
      className="block w-full rounded-lg bg-black"
      onTimeUpdate={(e) => onTimeUpdate?.(e.currentTarget.currentTime)}
    />
  );
});

export default VideoPlayer;
