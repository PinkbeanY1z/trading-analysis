export function panelBounds(width) {
  const available = Number.isFinite(width) ? width : 1024;
  return { min: 340, max: Math.max(340, Math.min(640, available - 488)) };
}

export function clampPanelWidth(value, width) {
  const { min, max } = panelBounds(width);
  return Math.round(Math.min(max, Math.max(min, Number.isFinite(value) ? value : 420)));
}

export function keyboardPanelWidth(key, current, width) {
  const { min, max } = panelBounds(width);
  const values = { ArrowLeft: current + 24, ArrowRight: current - 24, Home: min, End: max };
  return key in values ? clampPanelWidth(values[key], width) : null;
}

export function shouldSubmit(event) {
  return event.key === 'Enter' && !event.shiftKey && !event.isComposing && event.keyCode !== 229;
}
