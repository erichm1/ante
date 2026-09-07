function gotoWithParam(name, value) {
  const params = new URLSearchParams(location.search);
  params.set(name, value);
  if (name !== 'page') params.delete('page'); // changing page size (or a filter) resets to page 1
  location.href = '?' + params.toString();
}
