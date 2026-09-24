import { Pause, Play } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { usePauseQueue, useResumeQueue, useSystem } from "@/api/queries";
import { describeError } from "@/lib/errors";

export function QueueButton() {
  const sys = useSystem();
  const pause = usePauseQueue();
  const resume = useResumeQueue();
  const paused = sys.data?.queue.paused ?? false;
  const busy = pause.isPending || resume.isPending;
  const act = () =>
    (paused ? resume : pause).mutateAsync(undefined as never).then(
      () => toast.success(paused ? "佇列已繼續" : "佇列已暫停：目前的段落跑完後停下"),
      (e) => toast.error(describeError(e)),
    );
  return (
    <Button variant="outline" size="sm" onClick={act} disabled={busy || !sys.data}>
      {paused ? <Play /> : <Pause />}
      {paused ? "繼續佇列" : "暫停佇列"}
    </Button>
  );
}
