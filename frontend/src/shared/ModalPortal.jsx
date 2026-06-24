import { createPortal } from "react-dom";

// Render modal overlays at <body> instead of inline in the component tree.
// Mirrors what static/modal.js does for legacy `.modal-overlay` markup: a
// fixed, full-viewport overlay must NOT live inside a positioned / overflow /
// stacking-context ancestor or it gets trapped. The class page mounts the
// Today island inside `.task-fab-panel` (position: fixed; overflow: auto;
// z-index), which would otherwise pin the modal behind the header and clip
// its title. Portaling to body sidesteps all of that everywhere.
export default function ModalPortal({ children }) {
  return createPortal(children, document.body);
}
