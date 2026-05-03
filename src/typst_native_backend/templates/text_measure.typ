#import "/utils/anchor.typ": anchor-helper
#import "@preview/mitex:0.2.7": mi

#set page(margin: 0pt)
#set text(font: ${text_font}, size: ${font_size}, top-edge: ${text_top_edge})

${measure_preamble}

#let (create: anc, findpos: find) = anchor-helper("measure")
#let zws = [#sym.zws]

#context [
  #set par(leading: ${par_leading}, spacing: ${par_spacing})
  #grid(
    columns: 2,
    rows: 3,
    [#zws#anc(0)], [],
    [#zws#anc(1)${text_body}], anc(2),
    zws, anc(3),
  )
]

#context [#metadata((
  p0: (x: find(0).x.pt(), y: find(0).y.pt()),
  p1: (x: find(1).x.pt(), y: find(1).y.pt()),
  p2: (x: find(2).x.pt(), y: find(2).y.pt()),
  p3: (x: find(3).x.pt(), y: find(3).y.pt()),
)) <measure>]
