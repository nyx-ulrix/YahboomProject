// Wraps every grid widget with a glass card surface, drag header, lock toggle, close button.
import { forwardRef } from 'react';
import { GripHorizontal, Lock, LockOpen, X } from 'lucide-react';
import { useLayoutStore } from '../store';
import type { LayoutStore } from '../store';
import type { WidgetDefinition } from '../types';

interface Props {
  def: WidgetDefinition;
  children: React.ReactNode;
  style?: React.CSSProperties;
  className?: string;
}

export const WidgetWrapper = forwardRef<HTMLDivElement, Props & React.HTMLAttributes<HTMLDivElement>>(
  function WidgetWrapper({ def, children, style, className, ...rest }, ref) {
    const removeWidget = useLayoutStore((s: LayoutStore) => s.removeWidget);
    const toggleLock   = useLayoutStore((s: LayoutStore) => s.toggleLock);
    const locked       = useLayoutStore((s: LayoutStore) => s.lockedIds.includes(def.id));

    return (
      <div
        ref={ref}
        style={style}
        className={`${className ?? ''} w-full h-full`}
        {...rest}
      >
        <div
          className="w-full h-full flex flex-col"
          style={{
            background: 'var(--bg-surface)',
            border: '1px solid var(--stroke-subtle)',
            backdropFilter: 'blur(20px)',
            WebkitBackdropFilter: 'blur(20px)',
            borderRadius: 12,
            boxShadow: 'inset 0 1px 0 rgba(255,255,255,0.06)',
            overflow: 'hidden',
          }}
        >
          {/* ── Drag header ── full-width so the whole top bar initiates a drag. */}
          <div
            className={`${locked ? '' : 'widget-drag-handle'} flex-shrink-0 flex items-center justify-between px-1.5`}
            style={{
              height: 22,
              cursor: locked ? 'default' : 'grab',
              borderBottom: '1px solid var(--stroke-subtle)',
              background: 'rgba(255,255,255,0.02)',
            }}
          >
            {/* Grip indicator — only shown when unlocked */}
            <div style={{ flex: 1 }} />
            {!locked && (
              <GripHorizontal
                size={14}
                style={{ color: 'var(--stroke-strong)', opacity: 0.45, pointerEvents: 'none' }}
              />
            )}
            {locked && (
              <span style={{ fontSize: 9, color: 'var(--accent-purple)', letterSpacing: '0.08em', opacity: 0.7 }}>
                LOCKED
              </span>
            )}
            <div className="flex items-center gap-1" style={{ flex: 1, justifyContent: 'flex-end' }}>
              {/* Lock toggle */}
              <div
                className="widget-no-drag rounded p-0.5"
                style={{
                  color: locked ? 'var(--accent-purple)' : 'var(--text-secondary)',
                  cursor: 'pointer',
                  background: locked ? 'rgba(139,92,246,0.16)' : 'transparent',
                  border: `1px solid ${locked ? 'var(--accent-purple)' : 'transparent'}`,
                  boxShadow: locked ? '0 0 6px var(--glow-color)' : 'none',
                  lineHeight: 0,
                }}
                title={locked ? 'Click to unlock' : 'Click to lock'}
                onClick={() => toggleLock(def.id)}
              >
                {locked ? <Lock size={11} /> : <LockOpen size={11} />}
              </div>

              {/* Remove */}
              <button
                className="widget-no-drag rounded p-0.5 transition-colors"
                style={{
                  color: 'var(--text-secondary)',
                  background: 'transparent',
                  border: '1px solid transparent',
                  cursor: 'pointer',
                  lineHeight: 0,
                }}
                onMouseDown={(e) => e.stopPropagation()}
                onClick={(e) => { e.stopPropagation(); removeWidget(def.id); }}
                title="Remove widget"
              >
                <X size={11} />
              </button>
            </div>
          </div>

          {/* ── Content ── cancel zone so interactive widgets aren't accidentally dragged. */}
          <div className="widget-no-drag flex-1 min-h-0" style={{ padding: 8, overflow: 'hidden' }}>
            {children}
          </div>
        </div>
      </div>
    );
  }
);
