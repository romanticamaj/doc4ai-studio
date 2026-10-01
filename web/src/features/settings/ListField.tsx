import { useEffect, useState } from "react";
import { Textarea } from "@/components/ui/textarea";

type Props = Omit<React.ComponentProps<typeof Textarea>, "value" | "onChange"> & { value: string[]; onValue: (v: string[]) => void };

const toList = (t: string) => t.split(/\r?\n/).map((x) => x.trim()).filter(Boolean);

/** One entry per line. Keeps the typed text (blank lines, a half-typed entry) while reporting the clean list. */
export function ListField({ value, onValue, ...rest }: Props) {
  const [text, setText] = useState(value.join("\n"));
  useEffect(() => {
    setText((t) => (JSON.stringify(toList(t)) === JSON.stringify(value) ? t : value.join("\n")));
  }, [value]);
  return (
    <Textarea
      {...rest}
      value={text}
      spellCheck={false}
      onChange={(e) => {
        setText(e.target.value);
        onValue(toList(e.target.value));
      }}
    />
  );
}
