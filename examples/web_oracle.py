import json
from typing import Optional, List
from fastapi import FastAPI, Query, Path
from fastapi.testclient import TestClient
from pydantic import BaseModel, Field, EmailStr

class Register(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)
    name: Optional[str] = Field(None, max_length=255)
    location_lat: Optional[float] = Field(None, ge=-90, le=90)
    location_lng: Optional[float] = Field(None, ge=-180, le=180)
    age: Optional[int] = Field(None, ge=0, lt=150)
    newsletter: bool = False
    tags: List[str] = []

app = FastAPI()

@app.post("/api/v1/job_applicant/auth/register")
def register(body: Register):
    return body.model_dump()

@app.get("/jobs/search")
def search(q: str = Query(..., min_length=2, max_length=50), page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=100)):
    return {"q": q, "page": page, "limit": limit}

@app.get("/jobs/{job_id}")
def job(job_id: int = Path(..., ge=1)):
    return {"job_id": job_id}

@app.delete("/photos/{photo_id}")
def delete_photo(photo_id: int):
    return {"deleted": photo_id}

@app.get("/files/{owner_type}/{filename}")
def file(owner_type: str, filename: str):
    return {"owner_type": owner_type, "filename": filename}

c = TestClient(app, follow_redirects=False)
R = "/api/v1/job_applicant/auth/register"
cases = [
  ("ok", "POST", R, '{"email":"jane@example.com","password":"SecurePass123!"}'),
  ("ok_full", "POST", R, '{"email":"jane@example.com","password":"SecurePass123!","name":"Jane","location_lat":40.7128,"location_lng":-74.006,"age":30,"newsletter":true,"tags":["a","b"]}'),
  ("missing_both", "POST", R, '{}'),
  ("missing_password", "POST", R, '{"email":"jane@example.com"}'),
  ("short_password", "POST", R, '{"email":"jane@example.com","password":"short"}'),
  ("long_name", "POST", R, '{"email":"jane@example.com","password":"SecurePass123!","name":"' + "x"*256 + '"}'),
  ("bad_email", "POST", R, '{"email":"not-an-email","password":"SecurePass123!"}'),
  ("email_wrong_type", "POST", R, '{"email":42,"password":"SecurePass123!"}'),
  ("password_wrong_type", "POST", R, '{"email":"jane@example.com","password":12345678}'),
  ("lat_out_of_range", "POST", R, '{"email":"jane@example.com","password":"SecurePass123!","location_lat":91.5}'),
  ("lat_low", "POST", R, '{"email":"jane@example.com","password":"SecurePass123!","location_lat":-91}'),
  ("lat_string", "POST", R, '{"email":"jane@example.com","password":"SecurePass123!","location_lat":"north"}'),
  ("age_float", "POST", R, '{"email":"jane@example.com","password":"SecurePass123!","age":30.5}'),
  ("age_150", "POST", R, '{"email":"jane@example.com","password":"SecurePass123!","age":150}'),
  ("age_negative", "POST", R, '{"email":"jane@example.com","password":"SecurePass123!","age":-1}'),
  ("bool_wrong", "POST", R, '{"email":"jane@example.com","password":"SecurePass123!","newsletter":"maybe"}'),
  ("tags_not_list", "POST", R, '{"email":"jane@example.com","password":"SecurePass123!","tags":"a"}'),
  ("tags_bad_item", "POST", R, '{"email":"jane@example.com","password":"SecurePass123!","tags":["a",7]}'),
  ("null_name", "POST", R, '{"email":"jane@example.com","password":"SecurePass123!","name":null}'),
  ("null_password", "POST", R, '{"email":"jane@example.com","password":null}'),
  ("extra_field", "POST", R, '{"email":"jane@example.com","password":"SecurePass123!","role":"admin"}'),
  ("body_not_object", "POST", R, '[1,2]'),
  ("body_invalid_json", "POST", R, '{"email": '),
  ("body_empty", "POST", R, ''),
  ("two_errors", "POST", R, '{"email":"nope","password":"x"}'),
  ("not_found", "GET", "/nope", None),
  ("method_not_allowed", "GET", R, None),
  ("method_not_allowed_delete", "POST", "/photos/3", None),
  ("trailing_slash", "GET", "/jobs/5/", None),
  ("static_before_param", "GET", "/jobs/search?q=python", None),
  ("path_int_ok", "GET", "/jobs/5", None),
  ("path_int_bad", "GET", "/jobs/abc", None),
  ("path_int_zero", "GET", "/jobs/0", None),
  ("query_missing", "GET", "/jobs/search", None),
  ("query_short", "GET", "/jobs/search?q=p", None),
  ("query_limit_high", "GET", "/jobs/search?q=python&limit=101", None),
  ("query_page_bad", "GET", "/jobs/search?q=python&page=two", None),
  ("query_encoded", "GET", "/jobs/search?q=c%2B%2B+dev&page=2", None),
  ("two_params", "GET", "/files/employer/logo%20one.png", None),
  ("param_no_slash", "GET", "/files/employer/a/b.png", None),
  ("head_on_get", "HEAD", "/jobs/5", None),
]

P = '"password":"SecurePass123!"'
E = '"email":"jane@example.com"'
cases += [
  ("json_trailing_comma", "POST", R, '{"email":"a@b.co",}'),
  ("json_missing_colon", "POST", R, '{"email" "a@b.co"}'),
  ("json_missing_comma", "POST", R, '{"email":"a@b.co" "password":"x"}'),
  ("json_extra_data", "POST", R, '{} {}'),
  ("json_unterminated_string", "POST", R, '{"email":"a@b.co'),
  ("json_bad_escape", "POST", R, '{"email":"a\\qb"}'),
  ("json_control_char", "POST", R, '{"email":"a\tb"}'),
  ("json_single_quotes", "POST", R, "{'email':'a'}"),
  ("json_unclosed_array", "POST", R, '{"tags":["a"'),
  ("json_leading_zero", "POST", R, '{"age":01}'),
  ("json_bare_word", "POST", R, 'nope'),
  ("json_scalar_body", "POST", R, '42'),
  ("json_string_body", "POST", R, '"hi"'),
  ("json_null_body", "POST", R, 'null'),
  ("lax_int_from_string", "POST", R, '{%s,%s,"age":"30"}' % (E, P)),
  ("lax_int_from_float", "POST", R, '{%s,%s,"age":30.0}' % (E, P)),
  ("lax_int_from_bad_string", "POST", R, '{%s,%s,"age":"thirty"}' % (E, P)),
  ("lax_int_from_bool", "POST", R, '{%s,%s,"age":true}' % (E, P)),
  ("lax_float_from_string", "POST", R, '{%s,%s,"location_lat":"40.5"}' % (E, P)),
  ("lax_float_from_int", "POST", R, '{%s,%s,"location_lat":40}' % (E, P)),
  ("lax_float_from_bool", "POST", R, '{%s,%s,"location_lat":true}' % (E, P)),
  ("lax_bool_from_string", "POST", R, '{%s,%s,"newsletter":"yes"}' % (E, P)),
  ("lax_bool_from_int", "POST", R, '{%s,%s,"newsletter":1}' % (E, P)),
  ("lax_bool_from_int2", "POST", R, '{%s,%s,"newsletter":2}' % (E, P)),
  ("lax_bool_from_off", "POST", R, '{%s,%s,"newsletter":"OFF"}' % (E, P)),
  ("int_wrong_type_list", "POST", R, '{%s,%s,"age":[1]}' % (E, P)),
  ("float_wrong_type_obj", "POST", R, '{%s,%s,"location_lat":{}}' % (E, P)),
  ("bool_wrong_type_list", "POST", R, '{%s,%s,"newsletter":[]}' % (E, P)),
  ("str_from_bool", "POST", R, '{%s,%s,"name":true}' % (E, P)),
  ("password_unicode_8", "POST", R, '{%s,"password":"\u00e9\u00e9\u00e9\u00e9\u00e9\u00e9\u00e9\u00e9"}' % E),
  ("password_unicode_7", "POST", R, '{%s,"password":"ééééééé"}' % E),
  ("input_echo_escapes", "POST", R, '{%s,"password":"a\\"b\\\\\\n\\u00e9"}' % E),
  ("input_echo_whitespace", "POST", R, '{ "email" : "jane@example.com" ,\n "tags" : [ 1 , 2 ] }'),
  ("input_echo_nested", "POST", R, '{"email":{"a":[1,2.5,null,true,false,"x"]},%s}' % P),
  ("email_no_local", "POST", R, '{"email":"@example.com",%s}' % P),
  ("email_no_domain", "POST", R, '{"email":"jane@",%s}' % P),
  ("email_no_dot", "POST", R, '{"email":"jane@example",%s}' % P),
  ("email_two_at", "POST", R, '{"email":"ja@ne@example.com",%s}' % P),
  ("email_space", "POST", R, '{"email":"ja ne@example.com",%s}' % P),
  ("email_dot_start", "POST", R, '{"email":".jane@example.com",%s}' % P),
  ("email_double_dot", "POST", R, '{"email":"ja..ne@example.com",%s}' % P),
  ("email_domain_dash", "POST", R, '{"email":"jane@-example.com",%s}' % P),
  ("email_upper", "POST", R, '{"email":"Jane@EXAMPLE.com",%s}' % P),
  ("email_plus", "POST", R, '{"email":"jane+tag@sub.example.co.uk",%s}' % P),
  ("email_empty", "POST", R, '{"email":"",%s}' % P),
  ("email_tld_digit", "POST", R, '{"email":"jane@example.c0m",%s}' % P),
  ("email_domain_underscore", "POST", R, '{"email":"jane@exa_mple.com",%s}' % P),
  ("query_duplicate", "GET", "/jobs/search?q=aa&page=1&page=3", None),
  ("query_empty_value", "GET", "/jobs/search?q=", None),
  ("query_no_equals", "GET", "/jobs/search?q", None),
  ("query_limit_zero", "GET", "/jobs/search?q=aa&limit=0", None),
  ("query_int_float", "GET", "/jobs/search?q=aa&page=2.0", None),
  ("query_int_float_frac", "GET", "/jobs/search?q=aa&page=2.5", None),
  ("query_int_plus", "GET", "/jobs/search?q=aa&page=+2", None),
  ("query_int_spaces", "GET", "/jobs/search?q=aa&page=%202%20", None),
  ("query_int_negative", "GET", "/jobs/search?q=aa&page=-2", None),
  ("query_int_underscore", "GET", "/jobs/search?q=aa&page=1_0", None),
  ("query_long", "GET", "/jobs/search?q=" + "a"*51, None),
  ("query_utf8", "GET", "/jobs/search?q=%C3%A9%C3%A9", None),
  ("path_int_negative", "GET", "/jobs/-5", None),
  ("path_int_float", "GET", "/jobs/5.0", None),
  ("root_not_found", "GET", "/", None),
  ("slash_redirect_with_query", "GET", "/jobs/search/?q=aa", None),
  ("slash_redirect_405", "GET", "/photos/3/", None),
  ("double_slash", "GET", "//jobs/5", None),
  ("content_type_missing", "POST_NOCT", R, '{%s,%s}' % (E, P)),
]
out = []
for name, method, path, body in cases:
    headers = {"content-type": "application/json"} if body is not None else {}
    if method == "POST_NOCT":
        method, headers = "POST", {"content-type": "text/plain"}
    r = c.request(method, path, content=body.encode("utf-8") if body is not None else None, headers=headers)
    out.append({"content_type": headers.get("content-type",""), "name": name, "method": method, "path": path, "body": body, "status": r.status_code,
                "allow": r.headers.get("allow"), "location": r.headers.get("location"),
                "response": r.text})
json.dump(out, open("oracle.json", "w"), indent=1)
for o in out:
    print(o["name"], o["status"], o["allow"] or "", o["location"] or "", o["response"][:230])
