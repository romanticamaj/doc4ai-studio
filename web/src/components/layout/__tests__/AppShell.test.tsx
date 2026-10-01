import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AppShell } from "../AppShell";
import { getInitialTheme } from "@/lib/theme";

test("renders navigation and toggles dark mode", async () => {
  localStorage.clear();
  document.documentElement.classList.remove("dark");
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <AppShell>
          <div>content</div>
        </AppShell>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  for (const label of ["Convert", "Jobs", "Library", "Chunks", "MCP", "Settings"])
    expect(screen.getByRole("link", { name: label })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /theme/i }));
  expect(document.documentElement.classList.contains("dark")).toBe(true);
  expect(localStorage.getItem("aidoc_theme")).toBe("dark");
  expect(getInitialTheme()).toBe("dark");
});
