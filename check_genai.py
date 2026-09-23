import google.generativeai as genai
import inspect

try:
    print(inspect.signature(genai.GenerativeModel.generate_content))
    print(genai.GenerativeModel.generate_content.__doc__)
except Exception as e:
    print(e)
