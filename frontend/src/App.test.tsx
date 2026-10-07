import { render, screen } from "@testing-library/react";
import { App } from "./App";

// Smoke test: app renders and shows the default style-learning view.
test("renders app and the style-learning view by default", () => {
  render(<App />);
  expect(screen.getByRole("heading", { name: "MABLOP" })).toBeInTheDocument();
  expect(screen.getByRole("heading", { name: "스타일 학습" })).toBeInTheDocument();
});
