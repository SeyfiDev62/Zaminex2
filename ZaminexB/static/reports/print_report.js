/* Auto-opens the browser's native print dialog when the print-ready report
   page is ready, and keeps manual controls for when the user cancels it.
   Loaded from the «خروجی PDF» button of the SPA, so the dialog appears as a
   direct consequence of that click: the user then picks the destination
   (a real printer or «ذخیره به PDF») with the usual pages/color controls.
   The re-print button is what remains if the dialog is dismissed. */
(function () {
  "use strict";

  var started = false;

  var start = function () {
    if (started) return;
    started = true;
    try {
      window.print();
    } catch (e) {
      /* Nothing else to do — the report stays readable on the page. */
    }
  };

  var reprint = document.getElementById("reprint-btn");
  if (reprint) {
    reprint.addEventListener("click", function () {
      started = false; /* allow printing again after a cancel */
      start();
    });
  }

  var closeBtn = document.getElementById("close-btn");
  if (closeBtn) {
    closeBtn.addEventListener("click", function () {
      /* Works for a window opened by script; otherwise it is a no-op and
         the user simply closes the tab. */
      window.close();
    });
  }

  var settle = function () {
    /* A short pause after the fonts are in so the layout (font swap, table
       heights) has settled before the dialog snapshots the page. */
    setTimeout(start, 150);
  };

  /* Print only after the IRAN font has loaded — otherwise the dialog
     previews a fallback font and the saved PDF differs from the screen. */
  if (document.fonts && document.fonts.ready) {
    document.fonts.ready.then(settle)["catch"](settle);
  } else if (document.readyState === "complete") {
    settle();
  } else {
    window.addEventListener("load", settle);
  }

  /* Hard cap: a stuck font must never keep the dialog closed forever. */
  setTimeout(start, 4000);
})();
