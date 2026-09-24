import { useEffect, useState } from "react";
import { Input } from "@/components/ui/input";

type Props = Omit<React.ComponentProps<typeof Input>, "value" | "onChange" | "type"> & {
  value: number;
  onValue: (n: number) => void;
  min?: number;
  max?: number;
};

/**
 * An integer field that keeps what the user types while editing (clamping on every keystroke turned an emptied
 * field into the minimum, so typing "5" gave "15"). Valid numbers are reported as they are typed; the text is
 * clamped to [min, max] when the field loses focus.
 */
export function NumberField({ value, onValue, min, max, onBlur, ...rest }: Props) {
  const [text, setText] = useState(String(value));
  useEffect(() => {
    setText((t) => (Number(t) === value && t.trim() !== "" ? t : String(value)));
  }, [value]);

  const clamp = (n: number) => Math.min(max ?? Infinity, Math.max(min ?? -Infinity, Math.round(n)));

  return (
    <Input
      {...rest}
      type="number"
      inputMode="numeric"
      min={min}
      max={max}
      value={text}
      onChange={(e) => {
        const t = e.target.value;
        setText(t);
        const n = Number(t);
        if (t.trim() !== "" && Number.isFinite(n) && n === clamp(n)) onValue(n);
      }}
      onBlur={(e) => {
        const n = Number(text);
        const v = text.trim() === "" || !Number.isFinite(n) ? clamp(min ?? 0) : clamp(n);
        setText(String(v));
        if (v !== value) onValue(v);
        onBlur?.(e);
      }}
    />
  );
}
