import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";

const id = "a".repeat(32);
const response = { request_id: id, status: "answered", answer: "확인된 기록입니다.\n\n| 구분 | 상태 |\n| --- | --- |\n| 예약 | 확인 |", reason: "근거 확인", citations: [{ source_id: "S1", book_id: "AS001", file_name: "AS001.pdf", page: 2 }] };
const event = (data: object) => `data: ${JSON.stringify(data)}\n\n`;

test("answer, citation, PDF page navigation and close preserve the conversation", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.route("**/api/query/stream", route => route.fulfill({
    contentType: "text/event-stream", headers: { "X-Request-ID": id },
    body: event({ type: "start", request_id: id }) + event({ type: "answer_delta", request_id: id, delta: "확인 중" }) + event({ type: "final", request_id: id, response }),
  }));
  await page.route("**/api/documents/AS001", route => route.fulfill({ contentType: "application/pdf", body: readFileSync("tests/fixtures/document.pdf") }));
  await page.goto("/");
  await page.getByRole("textbox", { name: "질문", exact: true }).fill("예약 확인");
  await page.getByRole("button", { name: "질문 전송" }).click();
  await expect(page.getByText("답변 완료", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "AS001.pdf 2쪽 원문 열기" }).click();
  await expect(page.locator('.react-pdf__Page[data-page-number="2"] canvas')).toBeVisible();
  await expect(page.getByRole("spinbutton", { name: "페이지", exact: true })).toHaveValue("2");
  await page.getByRole("button", { name: "이전 페이지" }).click();
  await expect(page.locator('.react-pdf__Page[data-page-number="1"] canvas')).toBeVisible();
  await page.getByRole("button", { name: "출처로 돌아가기" }).click();
  await expect(page.getByRole("spinbutton", { name: "페이지", exact: true })).toHaveValue("2");
  await page.screenshot({ path: "test-results/chat-with-document.png", fullPage: true });
  await page.getByRole("button", { name: "문서 닫기" }).click();
  await expect(page.getByRole("complementary", { name: "원본 문서" })).toHaveCount(0);
  await expect(page.getByText("확인된 기록입니다.")).toBeVisible();
  await page.setViewportSize({ width: 320, height: 700 });
  const source = page.getByRole("button", { name: "AS001.pdf 2쪽 원문 열기" });
  await source.click();
  await expect(page.getByRole("complementary", { name: "원본 문서" })).toBeVisible();
  await expect(page.locator('.react-pdf__Page[data-page-number="2"] canvas')).toBeVisible();

  const toolbar = page.locator(".pdf-toolbar");
  expect(await toolbar.evaluate(el => el.scrollWidth)).toBe(await toolbar.evaluate(el => el.clientWidth));
  const previous = await page.getByRole("button", { name: "이전 페이지" }).boundingBox();
  const newTab = await page.getByRole("link", { name: "새 탭에서 원본 열기" }).boundingBox();
  expect(previous).not.toBeNull();
  expect(newTab).not.toBeNull();
  expect(Math.abs(previous!.y - newTab!.y)).toBeLessThan(2);

  const pageInput = page.getByRole("spinbutton", { name: "페이지", exact: true });
  await pageInput.fill("9");
  await expect(pageInput).toHaveValue("9");
  await pageInput.press("Enter");
  await expect(pageInput).toHaveValue("2");
  await pageInput.fill("1");
  await pageInput.press("Enter");
  await expect(page.locator('.react-pdf__Page[data-page-number="1"] canvas')).toBeVisible();

  await page.emulateMedia({ colorScheme: "dark" });
  await expect(page.locator("html")).toHaveCSS("color-scheme", "dark");
  await expect(page.locator(".react-pdf__Page")).toHaveCSS("background-color", "rgb(255, 255, 255)");
  await page.emulateMedia({ colorScheme: "light" });
  await expect(page.locator("html")).toHaveCSS("color-scheme", "light");
  await page.keyboard.press("Escape");
  await expect(source).toBeFocused();
  expect(errors).toEqual([]);
});

test("partial failure is not marked answered and retry is available", async ({ page }) => {
  await page.route("**/api/query/stream", route => route.fulfill({
    contentType: "text/event-stream", headers: { "X-Request-ID": id },
    body: event({ type: "start", request_id: id }) + event({ type: "answer_delta", request_id: id, delta: "임시 내용" }) + event({ type: "error", request_id: id, message: "실행 기록을 저장하지 못했습니다." }),
  }));
  await page.goto("/");
  await page.getByRole("textbox", { name: "질문", exact: true }).fill("예약 확인");
  await page.getByRole("button", { name: "질문 전송" }).click();
  await expect(page.getByRole("alert").filter({ hasText: "실행 기록을 저장하지 못했습니다." })).toContainText("실행 기록을 저장하지 못했습니다.");
  await expect(page.getByText("답변 완료", { exact: true })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "다시 답변" })).toBeVisible();
});

test("mobile layout and Korean composition do not accidentally send a question", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  let requests = 0;
  await page.route("**/api/query/stream", route => { requests++; return route.abort(); });
  await page.goto("/");
  const input = page.getByRole("textbox", { name: "질문", exact: true });
  await input.fill("예약");
  await input.dispatchEvent("compositionstart");
  await input.press("Enter");
  await input.dispatchEvent("compositionend");
  expect(requests).toBe(0);
  await expect(input).toHaveValue("예약\n");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.screenshot({ path: "test-results/chat-mobile.png", fullPage: true });
});


test("cancel preserves the next draft and does not retry automatically", async ({ page }) => {
  let release!: () => void;
  let requests = 0;
  const gate = new Promise<void>(resolve => { release = resolve; });
  await page.route("**/api/query/stream", async route => {
    requests++;
    await gate;
    await route.abort().catch(() => {});
  });
  try {
    await page.goto("/");
    const input = page.getByRole("textbox", { name: "질문", exact: true });
    await input.fill("첫 질문");
    await page.getByRole("button", { name: "질문 전송" }).click();
    const stop = page.getByRole("button", { name: "중단" });
    await expect(stop).toBeVisible();
    await input.fill("다음에 물어볼 질문");
    await stop.click();
    await expect(page.getByText("답변 생성을 중단했습니다.", { exact: true })).toBeVisible();
    await expect(input).toHaveValue("다음에 물어볼 질문");
    expect(requests).toBe(1);
  } finally { release(); }
});

test("missing original PDF displays a recoverable document error", async ({ page }) => {
  await page.route("**/api/query/stream", route => route.fulfill({
    contentType: "text/event-stream", headers: { "X-Request-ID": id },
    body: event({ type: "start", request_id: id }) + event({ type: "final", request_id: id, response }),
  }));
  await page.route("**/api/documents/AS001", route => route.fulfill({ status: 404, body: "missing" }));
  await page.goto("/");
  await page.getByRole("textbox", { name: "질문", exact: true }).fill("확인");
  await page.getByRole("button", { name: "질문 전송" }).click();
  await page.getByRole("button", { name: "AS001.pdf 2쪽 원문 열기" }).click();
  await expect(page.getByText("문서를 열지 못했습니다.", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "문서 닫기" }).click();
  await expect(page.getByText("답변 완료", { exact: true })).toBeVisible();
});


test("a long answer starts at its first paragraph on a narrow screen", async ({ page }) => {
  await page.setViewportSize({ width: 320, height: 700 });
  const paragraphs = Array.from({ length: 32 }, (_, index) => `확인 사항 ${index + 1}: 예약 확정 통지를 확인합니다.`);
  const answer = paragraphs.join("\n\n");
  await page.route("**/api/query/stream", route => route.fulfill({
    contentType: "text/event-stream",
    headers: { "X-Request-ID": id },
    body: event({ type: "start", request_id: id }) +
      event({ type: "final", request_id: id, response: { ...response, answer, citations: [] } }),
  }));
  await page.goto("/");
  await page.getByRole("textbox", { name: "질문", exact: true }).fill("예약 확인");
  await page.getByRole("button", { name: "질문 전송" }).click();
  await expect(page.getByText("답변 완료", { exact: true })).toBeVisible();
  await expect(page.getByText(paragraphs[0], { exact: true })).toBeInViewport({ ratio: 1 });
  await page.getByRole("button", { name: "최신 답변으로 이동" }).click();
  await expect(page.getByText(paragraphs.at(-1)!, { exact: true })).toBeInViewport({ ratio: 1 });
});


test("an overflowing draft scrolls beside the send button and survives a theme change", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" });
  await page.setViewportSize({ width: 390, height: 800 });
  await page.goto("/");

  const toggle = page.getByRole("switch", { name: "다크 모드" });
  const root = page.locator("html");
  const input = page.getByRole("textbox", { name: "질문", exact: true });
  const draft = "예약 번호와 출항 일정을 확인해 주세요.\n".repeat(16).trimEnd();
  await expect(toggle).toBeChecked();
  const emptyField = await input.evaluate(element => ({
    height: element.clientHeight,
    line: parseFloat(getComputedStyle(element).lineHeight),
  }));
  expect(emptyField.height).toBeGreaterThanOrEqual(emptyField.line * 2);
  await input.fill(draft);

  const scroll = await input.evaluate(element => {
    element.scrollTop = 40;
    return { top: element.scrollTop, height: element.scrollHeight, visible: element.clientHeight };
  });
  expect(scroll.height).toBeGreaterThan(scroll.visible);
  expect(scroll.top).toBeGreaterThan(0);
  const field = await input.boundingBox();
  const send = await page.getByRole("button", { name: "질문 전송" }).boundingBox();
  expect(field!.x + field!.width).toBeGreaterThan(send!.x + send!.width);

  await toggle.press("Space");
  await expect(toggle).not.toBeChecked();
  await expect(root).toHaveCSS("color-scheme", "light");
  await expect(input).toHaveValue(draft);
  await page.reload();
  await expect(root).toHaveCSS("color-scheme", "light");
  await expect(toggle).not.toBeChecked();
});
