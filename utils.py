import os
import re
import unicodedata

teutonic_chars = {
    ord('ä'): 'ae',
    ord('Ä'): 'Ae',
    ord('ü'): 'ue',
    ord('Ü'): 'Ue',
    ord('ö'): 'oe',
    ord('Ö'): 'Oe',
    ord('ß'): 'ss',
}

def env_value(env_var_name):
    val = os.getenv(env_var_name)
    if not val: raise Exception(f'Umgebungsvariable {env_var_name} muss gesetzt sein')
    return val

### String-Funktionen
def lower(val):
    if(val is None):
        return None
    if(isinstance(val, str)):
        return val.lower()
    return val

def singlify(input, character):
    pattern = character + '{2,}'
    return re.sub(pattern, character, input)

def unix_name(fullname):
    def isalnum_dash(ch):
        return ch.isalnum() or ch == '-'
    name = fullname.translate(teutonic_chars)
    # zusammengesetzte Zeichen in ihre Bestandteile zerlegen
    nfkd_form = unicodedata.normalize('NFKD', name)
    # diakritische Zeichen entfernen
    result = ''.join(char for char in nfkd_form if not unicodedata.combining(char))
    # alles außer Buchstaben, Ziffern und Bindestrich durch Unterstrich ersetzen
    cleaned_result = ''.join(char if isalnum_dash(char) else '_' for char in result)
    cleaned_result = singlify(cleaned_result, '_')

    # ß per casefold zu ss
    return cleaned_result.lower().casefold()
