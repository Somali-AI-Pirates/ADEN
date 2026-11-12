import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";

// Preserve Sanaebi's content policy: no raw HTML, external images or unsafe links.
export function MarkdownContent({ text }: { text: string }) {
  return <div className="markdown"><Markdown skipHtml remarkPlugins={[remarkGfm, remarkBreaks]}
    urlTransform={url => /^https?:\/\//i.test(url) ? url : ""}
    components={{
      a: ({ href, children }) => href
        ? <a href={href} target="_blank" rel="noopener noreferrer">{children}</a> : <>{children}</>,
      img: ({ alt }) => <>{alt || ""}</>,
      pre: ({ children }) => <pre tabIndex={0}>{children}</pre>,
      table: ({ children }) => <div className="table-scroll" tabIndex={0} role="region" aria-label="답변 표"><table>{children}</table></div>,
    }}>{text}</Markdown></div>;
}
