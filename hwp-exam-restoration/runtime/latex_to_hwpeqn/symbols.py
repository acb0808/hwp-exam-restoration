"""Documented Hancom commands; case is significant. See docs/latex-to-hwpeqn/source-coverage.md."""

GREEK = 'alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron pi rho sigma tau upsilon phi chi psi omega'.split()
SYMBOLS = {s: s for s in GREEK}
SYMBOLS.update({s.title(): s.title() for s in GREEK})
SYMBOLS.update({s: s for s in 'vartheta varpi varsigma varupsilon varphi varepsilon'.split()})
SYMBOLS.update(dict(
    aleph='ALEPH', hbar='HBAR', imath='IMATH', jmath='JMATH', ell='ELL', wp='WP',
    Re='ℜ', Im='IMAG', mho='MHO',
    sum='sum', prod='prod', coprod='COPROD', bigcup='UNION', bigcap='INTER',
    cup='SMALLUNION', cap='SMALLINTER', sqcap='SQCAP', sqcup='SQCUP',
    oplus='OPLUS', ominus='OMINUS', otimes='OTIMES', odot='ODOT', oslash='OSLASH',
    vee='VEE', lor='VEE', wedge='WEDGE', land='WEDGE',
    subset='SUBSET', supset='SUPSET', subseteq='SUBSETEQ', supseteq='SUPSETEQ',
    sqsubset='SQSUBSET', sqsupset='SQSUPSET', sqsubseteq='SQSUBSETEQ', sqsupseteq='SQSUPSETEQ',
    **{'in': 'IN'}, ni='OWNS', owns='OWNS', notin='notin',
    le='LEQ', leq='LEQ', ge='GEQ', geq='GEQ', ll='<<', gg='>>', lll='LLL', ggg='>>>',
    prec='PREC', succ='SUCC', uplus='UPLUS',
    pm='PLUSMINUS', mp='MINUSPLUS', times='TIMES', div='DIV', circ='CIRC',
    bullet='BULLET', cdot='CDOT', ast='AST', star='STAR', bigcirc='BIGCIRC',
    emptyset='EMPTYSET', varnothing='EMPTYSET', therefore='THEREFORE', because='BECAUSE',
    exists='EXIST', ne='neq', neq='neq', doteq='DOTEQ', sim='SIM', approx='APPROX',
    simeq='SIMEQ', cong='CONG', equiv='EQUIV', asymp='ASYMP', diamond='DIAMOND',
    forall='FORALL', prime='prime', partial='PARTIAL', infty='inf',
    neg='LNOT', lnot='LNOT', propto='PROPTO', dagger='DAGGER', ddagger='DDAGGER',
    leftarrow='larrow', gets='larrow', rightarrow='rarrow', to='rarrow',
    Leftarrow='LARROW', Rightarrow='RARROW', Leftrightarrow='LRARROW',
    leftrightarrow='lrarrow', uparrow='uparrow', downarrow='downarrow',
    Uparrow='UPARROW', Downarrow='DOWNARROW', updownarrow='udarrow', Updownarrow='UDARROW',
    nwarrow='nwarrow', searrow='searrow', nearrow='nearrow', swarrow='swarrow',
    hookleftarrow='hookleft', hookrightarrow='hookright', mapsto='mapsto',
    vert='vert', Vert='VERT', lvert='vert', rvert='vert', lVert='VERT', rVert='VERT',
    ldots='LDOTS', cdots='cdots', vdots='VDOTS', ddots='DDOTS',
    # Hancom equation format rev.1.3, section 2.1 uses literal U+25A1 in script.
    square='□', Box='□',
    triangle='TRIANGLE', triangledown='TRIANGLED', angle='ANGLE', measuredangle='MSANGLE',
    sphericalangle='SANGLE', vdash='VDASH', dashv='HLEFT', bot='BOT', perp='BOT',
    top='TOP', models='MODELS', degree='DEG',
    int='int', oint='oint', iint='DINT', iiint='TINT', oiint='ODINT', oiiint='OTINT',
))

FUNCTIONS = frozenset('sin cos tan cot sec cosec csc arcsin arccos arctan sinh cosh tanh coth log ln lg exp det gcd max min lim Lim Exp arc mod hom ker deg arg dim Pr'.split())
ACCENTS = dict(hat='hat', widehat='hat', check='check', widecheck='check', tilde='tilde',
               widetilde='tilde', acute='acute', grave='grave', dot='dot', ddot='ddot',
               bar='bar', overline='bar', vec='vec', overrightarrow='vec',
               overleftrightarrow='dyad', underline='under', overparen='arch')
FONTS = dict(mathrm='rm', mathit='it', mathbf='rmbold', boldsymbol='bold', bm='bold')
DECLARATIONS = dict(rm='rm', it='it', bf='rmbold')
SPACES = {',': '`', ':': '``', ';': '```', ' ': '~', 'quad': '~~', 'qquad': '~~~~',
          'enspace': '~', 'thinspace': '`', 'medspace': '``', 'thickspace': '```'}
DELIMITERS = {'(': '(', ')': ')', '[': '[', ']': ']', '|': '|', '.': '.',
              r'\{': '{', r'\}': '}', r'\lbrace': '{', r'\rbrace': '}',
              r'\langle': '<', r'\rangle': '>', r'\vert': '|', r'\lvert': '|', r'\rvert': '|',
              r'\Vert': 'VERT', r'\lVert': 'VERT', r'\rVert': 'VERT', r'\|': 'VERT',
              r'\lfloor': 'lfloor', r'\rfloor': 'rfloor', r'\lceil': 'lceil', r'\rceil': 'rceil',
              r'\backslash': '\\', '/': '/', r'\uparrow': 'uparrow', r'\downarrow': 'downarrow',
              r'\updownarrow': 'udarrow', r'\Uparrow': 'UPARROW', r'\Downarrow': 'DOWNARROW'}

UNICODE = dict(zip('αβγδεζηθικλμνξοπρστυφχψω', GREEK))
UNICODE.update(dict(zip('ΑΒΓΔΕΖΗΘΙΚΛΜΝΞΟΠΡΣΤΥΦΧΨΩ', [s.title() for s in GREEK])))
UNICODE.update({'−': '-', '×': 'TIMES', '÷': 'DIV', '±': 'PLUSMINUS', '∓': 'MINUSPLUS',
                '≤': 'LEQ', '≥': 'GEQ', '≠': 'neq', '∞': 'inf', '∈': 'IN', '∉': 'notin',
                '∑': 'sum', '∏': 'prod', '∫': 'int', '∂': 'PARTIAL',
                '→': 'rarrow', '←': 'larrow', '↔': 'lrarrow', '⇒': 'RARROW', '⇔': 'LRARROW',
                '∪': 'SMALLUNION', '∩': 'SMALLINTER', '∅': 'EMPTYSET', '·': 'CDOT',
                '∧': 'WEDGE', '∨': 'VEE', '¬': 'LNOT', '∀': 'FORALL', '∃': 'EXIST',
                'ϑ': 'vartheta', 'ϕ': 'varphi', 'ϖ': 'varpi', 'ς': 'varsigma', 'ϵ': 'varepsilon', '□': '□'})

# Unicode math alphabets retain letter identity; glyph metrics are font dependent.
_SCRIPT_EXCEPTIONS = dict(B='ℬ', E='ℰ', F='ℱ', H='ℋ', I='ℐ', L='ℒ', M='ℳ', R='ℛ')
_DOUBLE_EXCEPTIONS = dict(C='ℂ', H='ℍ', N='ℕ', P='ℙ', Q='ℚ', R='ℝ', Z='ℤ')
MATH_ALPHABETS = {
    'mathcal': {chr(65+i): _SCRIPT_EXCEPTIONS.get(chr(65+i), chr(0x1D49C+i)) for i in range(26)},
    'mathbb': {chr(65+i): _DOUBLE_EXCEPTIONS.get(chr(65+i), chr(0x1D538+i)) for i in range(26)},
}
SYMBOLS.update(nabla='NABLA', setminus='∖')
UNICODE.update({'∇': 'NABLA', '∖': '∖'})
