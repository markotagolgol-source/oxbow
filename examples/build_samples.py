#!/usr/bin/env python3
"""Write the two synthetic PDFs used by the tests and the README pictures.

harbor-picnic.pdf is a made-up dynamic XFA form.
club-note.pdf is an ordinary PDF with one text field.
Nothing in either file is a real person's form.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pikepdf

ROOT = Path(__file__).resolve().parents[1]


HARBOR_TEMPLATE = """<template xmlns="http://www.xfa.org/schema/xfa-template/3.3/">
  <subform name="PicnicSignup" layout="tb">
    <pageSet>
      <pageArea name="Front">
        <medium stock="letter" short="215.9mm" long="279.4mm"/>
        <occur max="1"/>
        <contentArea name="Body" x="18mm" y="28mm" w="180mm" h="228mm"/>
        <draw name="Band" x="0mm" y="0mm" w="215.9mm" h="16mm">
          <border><fill><color value="20,72,94"/></fill></border>
        </draw>
        <draw name="Mast" x="16mm" y="3.2mm" w="140mm" h="10mm">
          <font typeface="Arial" size="16pt" weight="bold"><color value="255,255,255"/></font>
          <value><text>Harbor Picnic Signup</text></value>
        </draw>
        <draw name="PageLabel" x="150mm" y="266mm" w="48mm" h="6mm">
          <font typeface="Arial" size="9pt"/>
          <para hAlign="right"/>
          <value><text>Page</text></value>
        </draw>
      </pageArea>
      <pageArea name="More">
        <medium stock="letter" short="215.9mm" long="279.4mm"/>
        <contentArea name="Body" x="18mm" y="28mm" w="180mm" h="228mm"/>
        <draw name="Band" x="0mm" y="0mm" w="215.9mm" h="16mm">
          <border><fill><color value="20,72,94"/></fill></border>
        </draw>
        <draw name="Mast" x="16mm" y="3.2mm" w="140mm" h="10mm">
          <font typeface="Arial" size="16pt" weight="bold"><color value="255,255,255"/></font>
          <value><text>Harbor Picnic Signup</text></value>
        </draw>
        <draw name="PageLabel" x="150mm" y="266mm" w="48mm" h="6mm">
          <font typeface="Arial" size="9pt"/>
          <para hAlign="right"/>
          <value><text>Page</text></value>
        </draw>
      </pageArea>
    </pageSet>
    <subform name="Sheet" layout="tb" w="180mm">
      <draw name="Intro" w="180mm" h="12mm">
        <font typeface="Arial" size="10pt"/>
        <value><text>A made-up signup sheet. Every name and dish here is invented.</text></value>
      </draw>
      <draw name="Gap1" w="180mm" h="4mm"/>
      <field name="GuestName" w="180mm" h="16mm">
        <ui><textEdit/></ui>
        <font typeface="Courier New" size="11pt"/>
        <para vAlign="middle"/>
        <caption placement="top" reserve="6mm">
          <font typeface="Arial" size="9pt" weight="bold"/>
          <value><text>Guest name</text></value>
        </caption>
        <border><edge thickness="0.6pt"><color value="40,70,80"/></edge></border>
      </field>
      <draw name="Gap2" w="180mm" h="4mm"/>
      <field name="Day" w="180mm" h="16mm">
        <ui><textEdit/></ui>
        <font typeface="Courier New" size="11pt"/>
        <para vAlign="middle"/>
        <caption placement="top" reserve="6mm">
          <font typeface="Arial" size="9pt" weight="bold"/>
          <value><text>Which day</text></value>
        </caption>
        <border><edge thickness="0.6pt"><color value="40,70,80"/></edge></border>
      </field>
      <draw name="Gap3" w="180mm" h="4mm"/>
      <field name="Dish" w="180mm" h="16mm">
        <ui><choiceList/></ui>
        <font typeface="Courier New" size="11pt"/>
        <para vAlign="middle"/>
        <caption placement="top" reserve="6mm">
          <font typeface="Arial" size="9pt" weight="bold"/>
          <value><text>Dish to share</text></value>
        </caption>
        <items><text>Salad</text><text>Bread</text><text>Fruit</text></items>
        <border><edge thickness="0.6pt"><color value="40,70,80"/></edge></border>
      </field>
      <draw name="Gap4" w="180mm" h="6mm"/>
      <field name="Coming" w="70mm" h="8mm">
        <ui><checkButton/></ui>
        <font typeface="Arial" size="10pt"/>
        <caption placement="right" reserve="56mm">
          <font typeface="Arial" size="10pt"/>
          <para vAlign="middle"/>
          <value><text>I am coming</text></value>
        </caption>
        <items><text>1</text><text>0</text></items>
      </field>
      <draw name="Gap5" w="180mm" h="6mm"/>
      <draw name="RideAsk" w="180mm" h="7mm">
        <font typeface="Arial" size="10pt" weight="bold"/>
        <value><text>Need a ride home?</text></value>
      </draw>
      <exclGroup name="Ride" layout="lr-tb" w="180mm">
        <field name="Yes" w="40mm" h="8mm">
          <ui><checkButton/></ui>
          <caption placement="right" reserve="28mm">
            <font typeface="Arial" size="10pt"/>
            <para vAlign="middle"/>
            <value><text>Yes</text></value>
          </caption>
          <items><text>1</text></items>
        </field>
        <field name="No" w="40mm" h="8mm">
          <ui><checkButton/></ui>
          <caption placement="right" reserve="28mm">
            <font typeface="Arial" size="10pt"/>
            <para vAlign="middle"/>
            <value><text>No</text></value>
          </caption>
          <items><text>2</text></items>
        </field>
      </exclGroup>
    </subform>
    <subform name="NotesPage" layout="tb" w="180mm">
      <breakBefore targetType="pageArea" startNew="1"/>
      <draw name="NoteIntro" w="180mm" h="10mm">
        <font typeface="Arial" size="12pt" weight="bold"/>
        <value><text>Anything else the host should know?</text></value>
      </draw>
      <draw name="Gap6" w="180mm" h="3mm"/>
      <field name="Note" w="180mm" h="36mm">
        <ui><textEdit multiLine="1"/></ui>
        <font typeface="Courier New" size="11pt"/>
        <caption placement="top" reserve="6mm">
          <font typeface="Arial" size="9pt" weight="bold"/>
          <value><text>Note</text></value>
        </caption>
        <border><edge thickness="0.6pt"><color value="40,70,80"/></edge></border>
      </field>
    </subform>
  </subform>
</template>
"""


def _packet(pdf, name: str, payload: bytes):
    return pikepdf.String(name), pdf.make_stream(payload)


def write_harbor(path: Path) -> None:
    """Dynamic XFA PDF. Datasets start empty. The template is the packet above."""
    pdf = pikepdf.Pdf.new()
    pdf.add_blank_page(page_size=(612, 792))
    template = HARBOR_TEMPLATE.encode("utf-8")
    datasets = (
        b'<xfa:datasets xmlns:xfa="http://www.xfa.org/schema/xfa-data/1.0/">'
        b"<xfa:data/></xfa:datasets>"
    )
    pairs = [
        _packet(pdf, "xdp:xdp", b'<xdp:xdp xmlns:xdp="http://ns.adobe.com/xdp/">'),
        _packet(pdf, "template", template),
        _packet(pdf, "datasets", datasets),
        _packet(pdf, "</xdp:xdp>", b"</xdp:xdp>"),
    ]
    array = pikepdf.Array()
    for name, stream in pairs:
        array.append(name)
        array.append(stream)
    pdf.Root.AcroForm = pikepdf.Dictionary(
        Fields=pikepdf.Array(),
        NeedAppearances=True,
        XFA=array,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf.save(path)
    pdf.close()


def write_club(path: Path) -> None:
    """Ordinary PDF. One AcroForm text field, no XFA."""
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))
    stream = b"""BT
/F1 20 Tf
72 700 Td
(Northside Book Club) Tj
/F1 12 Tf
0 -28 Td
(Leave a note for the next reader.) Tj
ET
0.25 0.32 0.36 RG
1.2 w
72 620 400 28 re S
BT
/F1 10 Tf
1 0 0 1 72 652 Tm
(Message) Tj
ET
"""
    page.Contents = pdf.make_stream(stream)
    page.Resources = pikepdf.Dictionary(
        Font=pikepdf.Dictionary(
            F1=pikepdf.Dictionary(
                Type=pikepdf.Name.Font,
                Subtype=pikepdf.Name.Type1,
                BaseFont=pikepdf.Name.Helvetica,
            )
        )
    )
    widget = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=pikepdf.Name.Annot,
            Subtype=pikepdf.Name.Widget,
            FT=pikepdf.Name.Tx,
            T=pikepdf.String("Message"),
            Rect=pikepdf.Array([72, 620, 472, 648]),
            F=4,
            DA=pikepdf.String("/Helv 12 Tf 0 g"),
        )
    )
    page.Annots = pikepdf.Array([widget])
    pdf.Root.AcroForm = pikepdf.Dictionary(
        Fields=pikepdf.Array([widget]),
        NeedAppearances=True,
        DA=pikepdf.String("/Helv 12 Tf 0 g"),
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    pdf.save(path)
    pdf.close()


def write_pictures(root: Path) -> None:
    """README pictures of the synthetic harbor form. No window chrome."""
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    import pikepdf as pike

    from oxbow.layout import load_document
    from oxbow.render import render_page

    examples = root / "examples"
    pdf = pike.open(examples / "harbor-picnic.pdf")
    pages = load_document(pdf)
    pdf.close()
    if len(pages) != 2:
        raise SystemExit(f"harbor form laid out {len(pages)} pages, expected 2")
    scale = 110 / 25.4
    blank = render_page(pages[0], {}, scale)
    blank.save(examples / "harbor-picnic.png")
    filled = {
        "PicnicSignup.Sheet.GuestName": "River Quinn",
        "PicnicSignup.Sheet.Day": "Saturday",
        "PicnicSignup.Sheet.Dish": "Salad",
        "PicnicSignup.Sheet.Coming": "1",
        "PicnicSignup.Sheet.Ride.Yes": "1",
        "PicnicSignup.NotesPage.Note": "I will bring cups.",
    }
    render_page(pages[0], filled, scale).save(examples / "harbor-picnic-filled.png")
    render_page(pages[1], filled, scale).save(examples / "harbor-picnic-note.png")
    print("pictures", blank.size)


def main():
    examples = ROOT / "examples"
    write_harbor(examples / "harbor-picnic.pdf")
    write_club(examples / "club-note.pdf")
    write_pictures(ROOT)
    print("wrote", examples / "harbor-picnic.pdf")
    print("wrote", examples / "club-note.pdf")


if __name__ == "__main__":
    main()
