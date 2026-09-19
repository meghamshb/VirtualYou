import {
  useEffect,
  useRef,
  type ReactNode,
  type ButtonHTMLAttributes,
} from "react";
import { X, ArrowRight, Check, Link2, ShieldCheck } from "lucide-react";
import { siGithub } from "simple-icons";
import type { ProviderId, Step } from "../../shared/model";
export function Button({
  children,
  variant = "secondary",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: "primary" | "secondary" | "ghost" | "danger";
}) {
  return (
    <button className={`button ${variant}`} {...props}>
      {children}
    </button>
  );
}
export function Brand({ provider }: { provider: ProviderId }) {
  switch (provider) {
    case "github":
      return (
        <span className="brand github">
          <svg viewBox="0 0 24 24" aria-hidden="true">
            <path fill="currentColor" d={siGithub.path} />
          </svg>
        </span>
      );
    case "slack":
      return (
        <span className="brand slack" aria-hidden="true">
          <svg viewBox="0 0 32 32">
            <path stroke="#36c5f0" d="M4 12h10M11 3v5" />
            <path stroke="#2eb67d" d="M20 4v10M29 11h-5" />
            <path stroke="#ecb22e" d="M28 20H18M21 29v-5" />
            <path stroke="#e01e5a" d="M12 28V18M3 21h5" />
          </svg>
        </span>
      );
    case "jira":
      return (
        <span className="brand jira">
          <svg viewBox="0 0 32 32" aria-hidden="true">
            <path fill="#2684ff" d="M12 3h17v17L12 3Z" />
            <path fill="#0052cc" d="M5 11h17v17L5 11Z" />
            <path fill="#73adff" d="m3 20 9-9 9 9-9 9Z" />
          </svg>
        </span>
      );
    case "drive":
      return (
        <span className="brand drive">
          <svg viewBox="0 0 32 32" aria-hidden="true">
            <path fill="#0f9d58" d="m12 3 6 10L8 29 2 19Z" />
            <path fill="#fbbc04" d="M12 3h10l10 17H20Z" />
            <path fill="#4285f4" d="M8 20h24l-6 9H2Z" />
          </svg>
        </span>
      );
  }
}
export function Modal({
  title,
  children,
  onClose,
}: {
  title: string;
  children: ReactNode;
  onClose: () => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement;
    const el = ref.current;
    el?.querySelector<HTMLElement>("button")?.focus();
    function key(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
      if (event.key === "Tab") {
        const nodes = Array.from(
          el?.querySelectorAll<HTMLElement>(
            'button:not(:disabled),input,select,textarea,[tabindex="0"]',
          ) || [],
        );
        const first = nodes[0],
          last = nodes.at(-1);
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }
    }
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("keydown", key);
      previous?.focus();
    };
  }, [onClose]);
  return (
    <div
      className="modal-backdrop"
      onMouseDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="modal-title"
        ref={ref}
      >
        <div className="modal-title">
          <h2 id="modal-title">{title}</h2>
          <Button variant="ghost" aria-label="Close dialog" onClick={onClose}>
            <X size={19} />
          </Button>
        </div>
        {children}
      </div>
    </div>
  );
}
export function Stepper({ step }: { step: Step }) {
  const index = step === "connect" ? 0 : step === "projects" ? 1 : 2;
  return (
    <ol className="stepper">
      {["Connect tools", "Choose projects", "Review & finish"].map(
        (label, i) => (
          <li
            key={label}
            className={i <= index ? "active" : ""}
            aria-current={i === index ? "step" : undefined}
          >
            <span className="step-number">
              {i < index ? <Check size={17} /> : i + 1}
            </span>
            <span>{label}</span>
            {i < 2 && <i />}
          </li>
        ),
      )}
    </ol>
  );
}
export function EmptyState({
  title,
  children,
}: {
  title: string;
  children: ReactNode;
}) {
  return (
    <div className="empty">
      <ShieldCheck size={32} />
      <h2>{title}</h2>
      <div>{children}</div>
    </div>
  );
}
export function Arrow() {
  return <ArrowRight size={17} />;
}
export function ConnectionIcon() {
  return <Link2 size={20} />;
}
