'use client';

import {
  useState,
  useMemo,
  useEffect,
  useRef,
  type KeyboardEvent,
  type FC,
} from 'react';
import type { ReactNode } from 'react';
import { motion, AnimatePresence } from 'motion/react';
// Icon package swapped for local SVGs; see ../../icons.jsx for the reasoning.
import { Search } from '../../icons.jsx';

export interface CommandItem {
  id: string;
  title: string;
  // Widened from the upstream three-value union: the ledger groups by
  // incidents, records and views, and the grouping code below is generic.
  section: string;
  icon: ReactNode;
  shortcut?: string;
  action: () => void;
}

interface Props {
  items?: CommandItem[];
}

export const CommandSearch: FC<Props> = ({ items = [] }) => {
  const [isOpen, setIsOpen] = useState(false);
  const [query, setQuery] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isOpen) {
      const timeout = setTimeout(() => {
        inputRef.current?.focus();
      }, 100);
      return () => clearTimeout(timeout);
    }
  }, [isOpen]);

  useEffect(() => {
    const handleKeyDown = (e: any) => {
      // Upstream binds a bare `f`, which fires while someone is simply reading
      // the page and collides with this dashboard's single-letter navigation
      // (g / i / a / v). Command-K is the convention and cannot collide.
      const isCmdK = e.key.toLowerCase() === 'k' && (e.metaKey || e.ctrlKey);
      if (isCmdK && !isOpen) {
        e.preventDefault();
        setIsOpen(true);
      }
      if (e.key === 'Escape' && isOpen) {
        e.preventDefault();
        e.stopPropagation();
        setIsOpen(false);
      }
    };
    // Use capture to catch the event before other listeners
    window.addEventListener('keydown', handleKeyDown, true);
    return () => window.removeEventListener('keydown', handleKeyDown, true);
  }, [isOpen]);

  const filteredItems = useMemo(() => {
    return items.filter((item) =>
      item.title.toLowerCase().includes(query.toLowerCase()),
    );
  }, [query, items]);

  useEffect(() => {
    requestAnimationFrame(() => setActiveIndex(0));
  }, [query]);

  const sections = useMemo(() => {
    const groups: { [key: string]: CommandItem[] } = {};
    filteredItems.forEach((item) => {
      if (!groups[item.section]) groups[item.section] = [];
      groups[item.section].push(item);
    });

    return Object.entries(groups).map(([name, items]) => ({
      name,
      items,
    }));
  }, [filteredItems]);

  const handleKeyDown = (e: KeyboardEvent) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveIndex((prev) => (prev + 1) % filteredItems.length);
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveIndex(
        (prev) => (prev - 1 + filteredItems.length) % filteredItems.length,
      );
    } else if (e.key === 'Enter') {
      const selectedItem = filteredItems[activeIndex];
      if (selectedItem) {
        selectedItem.action();
        setIsOpen(false);
      }
    }
  };

  const sharedTransition = {
    type: 'tween' as const,
    ease: 'easeOut' as const,
    duration: 0.15,
  };

  return (
    <>
      <AnimatePresence mode="popLayout">
        {isOpen && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="fixed inset-0 z-40 bg-black/50 backdrop-blur-[2px]"
              onClick={() => setIsOpen(false)}
            />
        )}
      </AnimatePresence>

      <div className="relative z-50 h-10 w-full max-w-[280px] md:w-64">
        <AnimatePresence mode="popLayout">
          {!isOpen ? (
            <motion.button
              key="trigger"
              layoutId="command-pallete"
              onClick={() => setIsOpen(true)}
              className="group absolute top-0 left-0 flex h-10 w-full items-center gap-3 overflow-hidden rounded-lg border border-[var(--border-strong)] bg-[var(--bg-raised)] px-4 py-2 text-[var(--text-muted)] shadow-sm hover:text-[var(--text)]"
              transition={sharedTransition}
            >
              <motion.div layoutId="search-icon" transition={sharedTransition}>
                <Search size={16} className="opacity-40" />
              </motion.div>
              <motion.span
                layoutId="search-text"
                transition={sharedTransition}
                className="pr-8 text-sm font-medium"
              >
                Search the ledger
              </motion.span>
              <motion.kbd
                layoutId="search-shortcut"
                transition={sharedTransition}
                className="absolute right-2 rounded border border-[var(--border-strong)] bg-[var(--bg-overlay)] px-2 py-0.5 text-[14px] font-bold text-[var(--text-dim)] group-hover:text-[var(--text)]"
              >
                ⌘K
              </motion.kbd>
            </motion.button>
          ) : (
            <motion.div
              layoutId="command-pallete"
              transition={sharedTransition}
              className="absolute -top-2 -left-2 z-50 flex h-80 w-xs flex-col overflow-hidden rounded-2xl border-[1.4px] border-[var(--border-strong)] bg-[var(--bg-raised)] shadow-[0_32px_64px_-15px_rgba(0,0,0,0.55)] md:w-[400px]"
              onClick={(e) => e.stopPropagation()}
            >
              {/* Search Header */}
              <div className="flex items-center border-b-[1.4px] border-[var(--border)] px-4 py-3.5">
                <motion.div
                  layoutId="search-icon"
                  transition={sharedTransition}
                >
                  <Search
                    size={18}
                    className="mr-3 text-[var(--text-dim)]"
                    strokeWidth={2.5}
                  />
                </motion.div>
                <div className="relative flex flex-1 items-center">
                  <input
                    ref={inputRef}
                    type="text"
                    className="w-full bg-transparent text-base font-medium text-[var(--text)] outline-none md:text-[15px]"
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    onKeyDown={handleKeyDown}
                  />
                  {!query && (
                    <motion.span
                      layoutId="search-text"
                      transition={sharedTransition}
                      className="pointer-events-none absolute left-0 text-[15px] font-medium text-[var(--text-dim)]"
                    >
                      Search incidents, records, actions…
                    </motion.span>
                  )}
                </div>
                <div className="ml-2 flex items-center gap-1.5">
                  <motion.span
                    layoutId="search-shortcut"
                    transition={sharedTransition}
                    className="rounded-[2px] border border-[var(--border-strong)] bg-[var(--bg-overlay)] p-0.5 px-1 text-[11px] font-bold text-[var(--text-dim)]"
                  >
                    Esc
                  </motion.span>
                </div>
              </div>

              {/* Results Body */}
              <div className="custom-scrollbar flex-1 overflow-y-auto p-1.5 md:max-h-[380px]">
                {filteredItems.length === 0 ? (
                  <div className="py-12 text-center text-sm text-[var(--text-muted)]">
                    No results found for "{query}"
                  </div>
                ) : (
                  <div className="space-y-4 py-1">
                    {sections.map((section) => (
                      <div key={section.name} className="space-y-1">
                        <h3 className="px-3 py-1 text-[11px] font-semibold tracking-wider text-[var(--text-dim)] uppercase">
                          {section.name}
                        </h3>
                        <div className="space-y-0.5">
                          {section.items.map((item) => {
                            const globalIndex = filteredItems.findIndex(
                              (fi) => fi.id === item.id,
                            );
                            const isActive = globalIndex === activeIndex;

                            return (
                              <button
                                key={item.id}
                                className={`group flex w-full items-center justify-between rounded-md px-3 py-2.5 text-left ${isActive ? 'bg-[var(--bg-overlay)] text-[var(--text)] ' : 'text-[var(--text-muted)] hover:text-[var(--text)] '} `}
                                onMouseEnter={() => setActiveIndex(globalIndex)}
                                onClick={() => {
                                  item.action();
                                  setIsOpen(false);
                                }}
                              >
                                <div className="flex items-center gap-3">
                                  <span
                                    className={`${isActive ? 'text-[var(--text)]' : 'text-[var(--text-dim)] group-hover:text-[var(--text)] '}`}
                                  >
                                    {item.icon}
                                  </span>
                                  <span className="text-[14px] leading-none font-medium">
                                    {item.title}
                                  </span>
                                </div>

                                {item.shortcut && (
                                  <kbd
                                    className={`rounded border px-1.5 py-0.5 text-[10px] font-bold ${isActive ? 'border-[var(--border-strong)] bg-[var(--bg-raised)] text-[var(--text-muted)] ' : 'border-transparent bg-transparent text-[var(--text-dim)] group-hover:text-[var(--text-muted)] '} `}
                                  >
                                    {item.shortcut}
                                  </kbd>
                                )}
                              </button>
                            );
                          })}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </>
  );
};
