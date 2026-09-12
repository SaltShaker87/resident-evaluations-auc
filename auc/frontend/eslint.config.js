// ESLint, flat config.
//
// Tuned to catch defects rather than to enforce a style: unused variables,
// undefined identifiers, and the React hook rules — which exist because
// getting them wrong produces bugs that only show up at runtime, in the
// browser, usually in front of a committee.
//
//   npm run lint
//
// It is also run by auc/check.sh alongside the Python checks.

import js from '@eslint/js';
import react from 'eslint-plugin-react';
import reactHooks from 'eslint-plugin-react-hooks';
import globals from 'globals';

export default [
  {
    ignores: ['dist/**', 'node_modules/**', 'public/**'],
  },
  js.configs.recommended,
  {
    files: ['**/*.{js,jsx}'],
    languageOptions: {
      ecmaVersion: 2022,
      sourceType: 'module',
      globals: { ...globals.browser, ...globals.es2021 },
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
    },
    settings: {
      react: { version: 'detect' },
    },
    plugins: {
      react,
      'react-hooks': reactHooks,
    },
    rules: {
      ...react.configs.flat.recommended.rules,
      ...reactHooks.configs.recommended.rules,

      // This project does not use TypeScript or prop-types, and adding either
      // is a bigger decision than a lint rule.
      'react/prop-types': 'off',

      // The JSX transform means React does not need importing to use JSX.
      'react/react-in-jsx-scope': 'off',

      // An underscore prefix is the conventional way to say "deliberately
      // unused", e.g. a caught error that is not inspected.
      'no-unused-vars': ['error', {
        argsIgnorePattern: '^_',
        varsIgnorePattern: '^_',
        caughtErrorsIgnorePattern: '^_',
      }],
    },
  },
];
