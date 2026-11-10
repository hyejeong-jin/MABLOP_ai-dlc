import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
import { LoginView } from "./LoginView";
import { ApiRequestError } from "../api";

// LoginView reads login/register/notice through useAuth(); the tests control them.
const auth = vi.hoisted(() => ({
  login: vi.fn(),
  register: vi.fn(),
  notice: null as string | null,
}));
vi.mock("../auth/AuthContext", () => ({
  useAuth: () => ({
    user: null,
    notice: auth.notice,
    login: auth.login,
    register: auth.register,
    logout: () => {},
    replaceToken: () => {},
  }),
}));

beforeEach(() => {
  auth.login.mockReset().mockResolvedValue(undefined);
  auth.register.mockReset().mockResolvedValue("가입 신청이 접수되었습니다.");
  auth.notice = null;
});

function field(label: string): HTMLInputElement {
  return screen.getByLabelText(label) as HTMLInputElement;
}

function type(label: string, value: string) {
  fireEvent.change(field(label), { target: { value } });
}

function openRegister() {
  fireEvent.click(screen.getByRole("tab", { name: "가입 신청" }));
}

function submitButton() {
  return screen.getByRole("button", { name: /^(로그인|가입 신청|처리 중\.\.\.)$/, hidden: false }) as HTMLButtonElement;
}

function submit() {
  fireEvent.submit(document.querySelector("form") as HTMLFormElement);
}

test("shows both mode tabs and starts in login mode with ID and password only", () => {
  render(<LoginView />);

  expect(screen.getByRole("tab", { name: "로그인" })).toHaveAttribute("aria-selected", "true");
  expect(screen.getByRole("tab", { name: "가입 신청" })).toHaveAttribute("aria-selected", "false");
  expect(field("아이디")).toBeInTheDocument();
  expect(field("비밀번호")).toBeInTheDocument();
  expect(screen.queryByLabelText("이름")).toBeNull();
  expect(screen.queryByLabelText("비밀번호 확인")).toBeNull();
});

test("registration mode adds the name and password confirmation fields", () => {
  render(<LoginView />);
  openRegister();

  expect(screen.getByRole("tab", { name: "가입 신청" })).toHaveAttribute("aria-selected", "true");
  expect(field("이름")).toBeInTheDocument();
  expect(field("아이디")).toBeInTheDocument();
  expect(field("비밀번호")).toBeInTheDocument();
  expect(field("비밀번호 확인")).toBeInTheDocument();
});

test("password inputs are masked and use the right autocomplete values per mode", () => {
  render(<LoginView />);

  expect(field("비밀번호")).toHaveAttribute("type", "password");
  expect(field("비밀번호")).toHaveAttribute("autocomplete", "current-password");
  expect(field("아이디")).toHaveAttribute("autocomplete", "username");

  openRegister();

  expect(field("비밀번호")).toHaveAttribute("type", "password");
  expect(field("비밀번호")).toHaveAttribute("autocomplete", "new-password");
  expect(field("비밀번호 확인")).toHaveAttribute("type", "password");
  expect(field("비밀번호 확인")).toHaveAttribute("autocomplete", "new-password");
  expect(field("이름")).toHaveAttribute("autocomplete", "name");
});

test("login sends the trimmed, lower-cased ID with the password", async () => {
  render(<LoginView />);
  type("아이디", "  Alice_01 ");
  type("비밀번호", "correct-horse-1");
  submit();

  await waitFor(() => expect(auth.login).toHaveBeenCalledWith("alice_01", "correct-horse-1"));
});

test("mismatched passwords show the Korean message and send no request", () => {
  render(<LoginView />);
  openRegister();
  type("이름", "앨리스");
  type("아이디", "alice");
  type("비밀번호", "password-one-1");
  type("비밀번호 확인", "password-two-2");
  submit();

  expect(screen.getByRole("alert")).toHaveTextContent("비밀번호가 일치하지 않습니다.");
  expect(auth.register).not.toHaveBeenCalled();
  expect(auth.login).not.toHaveBeenCalled();
});

test("successful registration shows the backend message and returns to login mode", async () => {
  auth.register.mockResolvedValue("가입 신청이 접수되었습니다. 관리자 승인 후 로그인할 수 있습니다.");
  render(<LoginView />);
  openRegister();
  type("이름", " 앨리스 ");
  type("아이디", "Alice");
  type("비밀번호", "password-one-1");
  type("비밀번호 확인", "password-one-1");
  submit();

  const status = await screen.findByRole("status");
  expect(status).toHaveTextContent("가입 신청이 접수되었습니다. 관리자 승인 후 로그인할 수 있습니다.");
  expect(auth.register).toHaveBeenCalledWith("alice", "앨리스", "password-one-1");
  expect(auth.login).not.toHaveBeenCalled();
  expect(screen.getByRole("tab", { name: "로그인" })).toHaveAttribute("aria-selected", "true");
  expect(screen.queryByLabelText("비밀번호 확인")).toBeNull();
  expect(screen.queryByRole("alert")).toBeNull();
});

test("the submit button is disabled and reads 처리 중... while a request runs", async () => {
  let resolveLogin: () => void = () => {};
  auth.login.mockImplementation(
    () =>
      new Promise<void>((resolve) => {
        resolveLogin = resolve;
      }),
  );
  render(<LoginView />);
  type("아이디", "alice");
  type("비밀번호", "correct-horse-1");
  expect(submitButton()).not.toBeDisabled();
  submit();

  await waitFor(() => expect(submitButton()).toBeDisabled());
  expect(submitButton()).toHaveTextContent("처리 중...");

  // A second submit while pending does not send another request.
  submit();
  expect(auth.login).toHaveBeenCalledTimes(1);

  resolveLogin();
  await waitFor(() => expect(submitButton()).not.toBeDisabled());
  expect(submitButton()).toHaveTextContent("로그인");
});

test("the submit button is also disabled while registration runs", async () => {
  let resolveRegister: (message: string) => void = () => {};
  auth.register.mockImplementation(
    () =>
      new Promise<string>((resolve) => {
        resolveRegister = resolve;
      }),
  );
  render(<LoginView />);
  openRegister();
  type("이름", "앨리스");
  type("아이디", "alice");
  type("비밀번호", "password-one-1");
  type("비밀번호 확인", "password-one-1");
  submit();

  await waitFor(() => expect(submitButton()).toBeDisabled());
  expect(submitButton()).toHaveTextContent("처리 중...");

  resolveRegister("접수되었습니다.");
  expect(await screen.findByRole("status")).toHaveTextContent("접수되었습니다.");
});

test("shows the Korean message of an ApiRequestError in an alert", async () => {
  auth.login.mockRejectedValue(new ApiRequestError(401, "unauthorized", "아이디 또는 비밀번호가 올바르지 않습니다."));
  render(<LoginView />);
  type("아이디", "alice");
  type("비밀번호", "wrong-password-1");
  submit();

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("아이디 또는 비밀번호가 올바르지 않습니다.");
  expect(submitButton()).not.toBeDisabled();
});

test("shows a Korean fallback message when the error is not an ApiRequestError", async () => {
  auth.login.mockRejectedValue(new TypeError("Failed to fetch"));
  render(<LoginView />);
  type("아이디", "alice");
  type("비밀번호", "correct-horse-1");
  submit();

  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("요청을 처리하지 못했습니다. 잠시 후 다시 시도하세요.");
  expect(alert).not.toHaveTextContent("Failed to fetch");
});

test("shows registration errors from the backend in an alert and stays in registration mode", async () => {
  auth.register.mockRejectedValue(new ApiRequestError(409, "conflict", "이미 사용 중인 아이디입니다."));
  render(<LoginView />);
  openRegister();
  type("이름", "앨리스");
  type("아이디", "alice");
  type("비밀번호", "password-one-1");
  type("비밀번호 확인", "password-one-1");
  submit();

  expect(await screen.findByRole("alert")).toHaveTextContent("이미 사용 중인 아이디입니다.");
  expect(screen.getByRole("tab", { name: "가입 신청" })).toHaveAttribute("aria-selected", "true");
});

test("shows the session-expiry notice as a status message", () => {
  auth.notice = "로그인이 만료되었습니다. 다시 로그인하세요.";
  render(<LoginView />);

  expect(screen.getByRole("status")).toHaveTextContent("로그인이 만료되었습니다. 다시 로그인하세요.");
});

test("shows whether Caps Lock is on or off once a key is pressed in the password field", () => {
  render(<LoginView />);
  expect(screen.getByText(/Caps Lock 상태는 입력을 시작하면 표시돼요/)).toBeInTheDocument();
  const password = field("비밀번호");
  fireEvent.keyDown(password, { key: "A", modifierCapsLock: true });
  expect(screen.getByText(/Caps Lock 켜짐/)).toBeInTheDocument();
  fireEvent.keyUp(password, { key: "a", modifierCapsLock: false });
  expect(screen.getByText("Caps Lock 꺼짐")).toBeInTheDocument();
});

test("비밀번호를 잊으셨나요? opens a request mail for the administrator", () => {
  render(<LoginView />);
  type("아이디", "alice01");
  fireEvent.click(screen.getByRole("button", { name: "비밀번호를 잊으셨나요?" }));
  const dialog = screen.getByRole("dialog", { name: "비밀번호 찾기" });
  const mail = screen.getByRole("link", { name: /관리자에게 메일 보내기/ }) as HTMLAnchorElement;
  expect(dialog).toContainElement(mail);
  expect(mail.href.startsWith("mailto:hyejeong.data@gmail.com")).toBe(true);
  expect(decodeURIComponent(mail.href)).toContain("아이디: alice01");
  fireEvent.click(screen.getAllByRole("button", { name: "닫기" })[0]);
  expect(screen.queryByRole("dialog")).toBeNull();
});

test("the footer shows the copyright, the signature and the contact address", () => {
  render(<LoginView />);
  expect(screen.getByText(/Made with care by/)).toHaveTextContent("hyejeong.jin");
  expect(screen.getByRole("link", { name: "hyejeong.data@gmail.com" })).toHaveAttribute("href", "mailto:hyejeong.data@gmail.com");
});

test("the forgot-password link is only offered in login mode", () => {
  render(<LoginView />);
  openRegister();
  expect(screen.queryByRole("button", { name: "비밀번호를 잊으셨나요?" })).toBeNull();
});