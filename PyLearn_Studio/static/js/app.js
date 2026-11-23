// Small client-side helpers for PyLearn Studio.
document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("a[href]").forEach(link => {
    link.addEventListener("click", () => {
      link.classList.add("navigating");
    });
  });

  // Quiz answer persistence.
  // Answers are stored by question name, not by the currently visible question.
  // This means changing/paginating/re-rendering questions cannot erase earlier answers.
  const quizForm = document.getElementById("quizForm");
  if (!quizForm) return;

  const moduleId = quizForm.dataset.quizModule || "unknown";
  const storageKey = `pylearn_quiz_answers_${moduleId}`;
  const status = document.getElementById("quizSaveStatus");
  const answerSelector = 'input[type="radio"][name^="q"]';

  const readAnswers = () => {
    try {
      const raw = sessionStorage.getItem(storageKey);
      return raw ? JSON.parse(raw) : {};
    } catch (error) {
      return {};
    }
  };

  const writeAnswers = (answers) => {
    try {
      sessionStorage.setItem(storageKey, JSON.stringify(answers));
      return true;
    } catch (error) {
      return false;
    }
  };

  const restoreAnswers = () => {
    const answers = readAnswers();
    quizForm.querySelectorAll(answerSelector).forEach(input => {
      input.checked = answers[input.name] === input.value;
    });
  };

  // Restore immediately and whenever the browser returns to this page.
  restoreAnswers();
  window.addEventListener("pageshow", restoreAnswers);

  // Event delegation is intentional: it also works if a question navigator
  // replaces/rebuilds the question elements later.
  quizForm.addEventListener("change", (event) => {
    const input = event.target;
    if (!input.matches(answerSelector)) return;

    const answers = readAnswers();
    answers[input.name] = input.value;
    if (writeAnswers(answers)) {
      if (status) status.textContent = "Answer saved.";
    } else if (status) {
      status.textContent = "Answer selected.";
    }
  });

  // Keep saved answers synchronized if another script changes the question DOM.
  const observer = new MutationObserver(() => restoreAnswers());
  observer.observe(quizForm, { childList: true, subtree: true });

  quizForm.addEventListener("submit", () => {
    // The browser serializes the checked radio buttons into POST data before
    // leaving the page. Clear only after this attempt has been submitted.
    try {
      sessionStorage.removeItem(storageKey);
    } catch (error) {
      // Storage errors must never block quiz submission.
    }
  });

});
