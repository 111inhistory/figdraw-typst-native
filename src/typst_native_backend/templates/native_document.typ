#import "@preview/mitex:0.2.7": mi

#set page(width: ${page_width}, height: ${page_height}, margin: ${page_margin})
#set text(font: ${text_font}, top-edge: ${text_top_edge})
#set par(leading: ${par_leading}, spacing: ${par_spacing})

${preamble}

${definitions}

#block(width: ${canvas_width}, height: ${canvas_height})[
${commands}
]
