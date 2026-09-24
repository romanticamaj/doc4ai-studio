import { expect, test, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { NumberField } from "../NumberField";

function Harness({ onValue }: { onValue: (n: number) => void }) {
  const [v, setV] = useState(7);
  return <NumberField aria-label="days" value={v} min={1} onValue={(n) => { setV(n); onValue(n); }} />;
}

test("clearing then typing 5 gives 5 (not 15); an empty field falls back to the minimum on blur", async () => {
  const onValue = vi.fn();
  render(<Harness onValue={onValue} />);
  const box = screen.getByRole("spinbutton", { name: "days" }) as HTMLInputElement;
  await userEvent.clear(box);
  expect(box.value).toBe("");
  await userEvent.type(box, "5");
  expect(box.value).toBe("5");
  expect(onValue).toHaveBeenLastCalledWith(5);
  await userEvent.clear(box);
  await userEvent.tab();
  expect(box.value).toBe("1");
  expect(onValue).toHaveBeenLastCalledWith(1);
});
