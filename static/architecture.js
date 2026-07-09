const walkTabs = [...document.querySelectorAll('.walk-tab')];
const walkPanels = [...document.querySelectorAll('.walk-panel')];
const walkProgress = document.getElementById('walkProgress');
const walkPrev = document.getElementById('walkPrev');
const walkNext = document.getElementById('walkNext');
let walkIndex = 0;

function setWalk(index) {
  walkIndex = Math.max(0, Math.min(walkPanels.length - 1, index));
  walkTabs.forEach((tab, i) => tab.classList.toggle('active', i === walkIndex));
  walkPanels.forEach((panel, i) => panel.classList.toggle('active', i === walkIndex));
  if (walkProgress) walkProgress.textContent = `${String(walkIndex + 1).padStart(2, '0')} / ${String(walkPanels.length).padStart(2, '0')}`;
  if (walkPrev) walkPrev.disabled = walkIndex === 0;
  if (walkNext) walkNext.disabled = walkIndex === walkPanels.length - 1;
}

walkTabs.forEach((tab, index) => tab.addEventListener('click', () => setWalk(index)));
if (walkPrev) walkPrev.addEventListener('click', () => setWalk(walkIndex - 1));
if (walkNext) walkNext.addEventListener('click', () => setWalk(walkIndex + 1));
setWalk(0);
