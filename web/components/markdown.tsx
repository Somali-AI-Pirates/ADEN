import type { Element, ElementContent, Root } from "hast";
import { createContext, useContext, type ComponentPropsWithoutRef } from "react";
import Markdown, { type ExtraProps } from "react-markdown";
import remarkCjkFriendly from "remark-cjk-friendly/parseOnly";
import remarkGfm from "remark-gfm";
import remarkBreaks from "remark-breaks";
import type { Citation } from "../lib/api";

function annotateCitations(numbers: ReadonlyMap<string, number>) {
  return (tree: Root) => {
    function visit(parent: Root | Element) {
      for (let index = 0; index < parent.children.length; index++) {
        const node = parent.children[index];
        if (node.type === "element" && !["a", "code", "pre", "button"].includes(node.tagName)) {
          visit(node);
        }
        if (node.type !== "text") continue;

        const children: ElementContent[] = [];
        let offset = 0;
        for (const match of node.value.matchAll(/\[([^\[\]\r\n]+)\]/g)) {
          const cited = match[1].split(",").map(id => numbers.get(id.trim()));
          if (cited.some(number => number === undefined)) continue;

          const preceding = node.value.slice(offset, match.index);
          if (preceding) children.push({ type: "text", value: preceding });
          for (const number of cited) {
            children.push({
              type: "element",
              tagName: "span",
              properties: { dataCitationNumber: number },
              children: [],
            });
          }
          offset = match.index + match[0].length;
        }
        if (!offset) continue;

        const trailing = node.value.slice(offset);
        if (trailing) children.push({ type: "text", value: trailing });
        parent.children.splice(index, 1, ...children);
        index += children.length - 1;
      }
    }

    visit(tree);
  };
}

type Props = {
  text: string;
  citations?: Citation[];
  activeSource: Citation | null;
  onOpenSource: (citation: Citation, trigger: HTMLButtonElement) => void;
};

const CitationContext = createContext<Omit<Props, "text"> | null>(null);

function CitationMarker({ node, children, ...props }: ComponentPropsWithoutRef<"span"> & ExtraProps) {
  const context = useContext(CitationContext);
  const number = node?.properties.dataCitationNumber;
  const citation = typeof number === "number" ? context?.citations?.[number - 1] : undefined;
  if (!citation || !context) return <span {...props}>{children}</span>;

  const label = `${citation.file_name} ${citation.page}쪽`;
  const { activeSource, onOpenSource } = context;
  const active = activeSource?.book_id === citation.book_id && activeSource.page === citation.page;
  return (
    <button
      type="button"
      className="inline-citation"
      title={label}
      aria-label={`인용 ${number} · ${label} 원문 열기`}
      aria-current={active || undefined}
      onClick={event => onOpenSource(citation, event.currentTarget)}
    >
      {number}
    </button>
  );
}

export function MarkdownContent({ text, citations, activeSource, onOpenSource }: Props) {
  const numbers = new Map(citations?.map((citation, index) => [citation.source_id, index + 1]));

  // Preserve Sanaebi's content policy: no raw HTML, external images or unsafe links.
  return (
    <div className="markdown">
      <CitationContext.Provider value={{ citations, activeSource, onOpenSource }}>
        <Markdown
          skipHtml
          remarkPlugins={[remarkCjkFriendly, [remarkGfm, { singleTilde: false }], remarkBreaks]}
          rehypePlugins={numbers.size ? [[annotateCitations, numbers]] : []}
          urlTransform={url => /^https?:\/\//i.test(url) ? url : ""}
          components={{
            a: ({ href, children }) => href
              ? <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>
              : <>{children}</>,
            img: ({ alt }) => <>{alt || ""}</>,
            pre: ({ children }) => <pre tabIndex={0}>{children}</pre>,
            table: ({ children }) => (
              <div className="table-scroll" tabIndex={0} role="region" aria-label="답변 표">
                <table>{children}</table>
              </div>
            ),
            span: CitationMarker,
          }}
        >
          {text}
        </Markdown>
      </CitationContext.Provider>
    </div>
  );
}
